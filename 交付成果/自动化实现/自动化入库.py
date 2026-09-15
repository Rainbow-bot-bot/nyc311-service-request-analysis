# 逐个读取 Parquet 分片，完成审查、清洗、分表并直接写入 MySQL。
import argparse
import re
import pyarrow.parquet as pq
from getpass import getpass
import os
from pathlib import Path
from urllib.parse import quote_plus

import pandas as pd
from sqlalchemy import create_engine, text, inspect

PACKAGE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("NYC311_DATA_DIR", PACKAGE_DIR / "原始数据"))
FIELD_FILE = PACKAGE_DIR / "字段说明.xlsx"
RULES_FILE = PACKAGE_DIR / "数据质量规则.xlsx"
ANALYSIS_SQL_FILE = PACKAGE_DIR.parent / "探索过程" / "生成初步分析结果.sql"

def mysql_settings():
    """从环境变量读取连接信息；密码缺失时交互输入。"""
    password = os.environ.get("NYC311_MYSQL_PASSWORD")
    if password is None:
        password = getpass("MySQL密码（不回显）：")
    return {
        "host": os.environ.get("NYC311_MYSQL_HOST", "localhost"),
        "port": int(os.environ.get("NYC311_MYSQL_PORT", "3306")),
        "user": os.environ.get("NYC311_MYSQL_USER", "root"),
        "password": password,
        "database": os.environ.get("NYC311_MYSQL_DATABASE", ""),
        "charset": "utf8mb4",
    }
DATA_SNAPSHOT_DATE = pd.Timestamp("2026-09-07 23:59:59")

# 按字段说明表统一筛选分析字段，避免在代码中反复手写字段名。
def select_analysis_fields(table, field_description):
    """根据字段说明表筛选分析层字段，并返回独立副本。

    Parameters
    ----------
    table : pandas.DataFrame
        待筛选的原始数据表。
    field_description : pandas.DataFrame
        字段说明表，必须包含“字段名”和“分析层处理”两列。
        “分析层处理”不等于“删除”的字段会被保留。

    Returns
    -------
    pandas.DataFrame
        只包含保留字段的数据表，字段顺序与字段说明表一致。

    Raises
    ------
    ValueError
        字段说明表要求保留的字段在原始数据中不存在时抛出。
    """
    # 字段说明表是字段取舍的唯一入口。
    selected = field_description["分析层处理"] != "删除"
    selected_fields = field_description.loc[selected, "字段名"].tolist()

    # 先检查字段是否齐全，避免筛选后才发现数据源缺列。
    missing_fields = []
    for field in selected_fields:
        if field not in table.columns:
            missing_fields.append(field)

    if len(missing_fields) > 0:
        raise ValueError(f"原始数据缺少字段：{missing_fields}")

    return table.loc[:, selected_fields].copy()


# 每种检查方式需要填写哪些参数。
REQUIRED_PARAMETERS = {
    "范围": ["最小值", "最大值"],
    "允许值": ["允许值"],
    "格式": ["正则表达式"],
    "不早于": ["对照字段"],
    "不晚于": ["对照字段"],
    "相同": ["对照字段"],
    "条件必填": ["对照字段"],
    "至少一个非空": ["对照字段"]
}
SUPPORTED_CHECKS = [
    "必填", "唯一", "日期", "数值", "整数"
] + list(REQUIRED_PARAMETERS)


# 先检查规则表能否执行，业务内容由规则编写者负责。
def validate_business_rules(rules, table, table_name):
    """检查业务规则表的基本结构，返回无法执行的规则。

    Parameters
    ----------
    rules : pandas.DataFrame
        从 Excel 读取的业务规则表。
    table : pandas.DataFrame
        准备接受检查的数据表。
    table_name : str
        当前数据表在规则表中的名称。

    Returns
    -------
    pandas.DataFrame
        规则填写错误；没有错误时返回空表。
    """
    columns = ["Excel行号", "规则编号", "错误原因"]
    errors = []
    required_columns = [
        "规则编号", "适用表", "字段名", "检查方式", "对照字段",
        "最小值", "最大值", "允许值", "正则表达式",
        "规则依据", "规则说明", "是否启用"
    ]

    for column in required_columns:
        if column not in rules.columns:
            errors.append([None, None, f"缺少必要列：{column}"])
    if len(errors) > 0:
        return pd.DataFrame(errors, columns=columns)

    duplicate_ids = rules["规则编号"].duplicated(keep=False)

    def is_blank(value):
        return pd.isna(value) or str(value).strip() == ""

    # 只检查程序运行必需的内容。
    for index in rules.index:
        rule = rules.loc[index]
        rule_id = rule["规则编号"]
        check = rule["检查方式"]
        row_errors = []

        if is_blank(rule_id):
            row_errors.append("规则编号不能为空")
        elif duplicate_ids.loc[index]:
            row_errors.append("规则编号重复")
        if rule["适用表"] != table_name:
            row_errors.append(f"适用表应为 {table_name}")
        if check not in SUPPORTED_CHECKS:
            row_errors.append(f"不支持的检查方式：{check}")
        if rule["是否启用"] not in ["是", "否"]:
            row_errors.append("是否启用只能填写‘是’或‘否’")

        if rule["是否启用"] == "是":
            field = rule["字段名"]
            if is_blank(field):
                row_errors.append("字段名不能为空")
            elif field not in table.columns:
                row_errors.append(f"数据表中不存在字段：{field}")

            for parameter in REQUIRED_PARAMETERS.get(check, []):
                if is_blank(rule[parameter]):
                    row_errors.append(f"{check}检查必须填写{parameter}")

            other_field = rule["对照字段"]
            if "对照字段" in REQUIRED_PARAMETERS.get(check, []):
                if not is_blank(other_field) and other_field not in table.columns:
                    row_errors.append(f"数据表中不存在对照字段：{other_field}")

            for field in ["规则依据", "规则说明"]:
                if is_blank(rule[field]):
                    row_errors.append(f"{field}不能为空")

        for reason in row_errors:
            errors.append([index + 2, rule_id, reason])

    return pd.DataFrame(errors, columns=columns)


# 把一条业务规则转换为适用范围掩码和错误掩码。
def check_rule(table, rule):
    """根据一条业务规则生成适用范围掩码和错误掩码。

    Parameters
    ----------
    table : pandas.DataFrame
        待审查的数据表。
    rule : pandas.Series
        数据质量规则表中的一行。必须提供“字段名”“检查方式”
        和“规则说明”；需要字段对比时还必须提供“对照字段”。

    Returns
    -------
    tuple
        返回 scope_mask、error_mask、error_type、error_reason。
        scope_mask 表示本规则需要检查的记录，error_mask 表示其中
        不符合规则的记录。

    Raises
    ------
    ValueError
        目标字段或对照字段不存在，或者“检查方式”不受支持时抛出。
    """
    column = rule["字段名"]
    check = rule["检查方式"]

    # 规则指定的字段不存在时停止，避免业务规则被静默跳过。
    if column not in table.columns:
        rule_id = rule["规则编号"]
        raise ValueError(f"规则 {rule_id} 的字段不存在：{column}")

    values = table[column]
    # 空字符串和只含空格的字符串也按空值处理。
    empty = values.isna() | (values.astype("string").str.strip() == "")
    all_records = pd.Series(True, index=table.index)

    # 先处理只检查一个字段的规则。
    if check == "必填":
        scope_mask = all_records
        error_mask = empty
        error_type = "完整性"

    elif check == "唯一":
        scope_mask = ~empty
        error_mask = scope_mask & values.duplicated(keep=False)
        error_type = "唯一性"

    elif check == "日期":
        # 临时转换只用于找出无法解析的非空值。
        converted = pd.to_datetime(values, errors="coerce")
        scope_mask = ~empty
        error_mask = scope_mask & converted.isna()
        error_type = "有效性"

    elif check == "数值":
        converted = pd.to_numeric(values, errors="coerce")
        scope_mask = ~empty
        error_mask = scope_mask & converted.isna()
        error_type = "有效性"

    elif check == "整数":
        converted = pd.to_numeric(values, errors="coerce")
        scope_mask = ~empty
        error_mask = scope_mask & (converted.isna() | (converted % 1 != 0))
        error_type = "有效性"

    elif check == "范围":
        converted = pd.to_numeric(values, errors="coerce")
        minimum = rule["最小值"]
        maximum = rule["最大值"]
        scope_mask = converted.notna()
        error_mask = scope_mask & ((converted < minimum) | (converted > maximum))
        error_type = "有效性"

    elif check == "允许值":
        allowed = str(rule["允许值"]).split("|")
        scope_mask = ~empty
        error_mask = scope_mask & ~values.isin(allowed)
        error_type = "有效性"

    elif check == "格式":
        pattern = rule["正则表达式"]
        scope_mask = ~empty
        error_mask = scope_mask & ~values.astype(str).str.match(pattern)
        error_type = "有效性"

    # 下面的规则需要另一个字段作为判断条件。
    elif check in ["不早于", "不晚于", "相同", "条件必填", "至少一个非空"]:
        other_column = rule["对照字段"]

        if other_column not in table.columns:
            rule_id = rule["规则编号"]
            raise ValueError(f"规则 {rule_id} 的对照字段不存在：{other_column}")

        other_values = table[other_column]
        other_empty = other_values.isna() | (other_values.astype("string").str.strip() == "")

        if check == "不早于":
            first_date = pd.to_datetime(values, errors="coerce")
            second_date = pd.to_datetime(other_values, errors="coerce")
            # 两个日期都能解析时才比较先后顺序。
            scope_mask = first_date.notna() & second_date.notna()
            error_mask = scope_mask & (first_date < second_date)
            error_type = "一致性"

        elif check == "不晚于":
            first_date = pd.to_datetime(values, errors="coerce")
            second_date = pd.to_datetime(other_values, errors="coerce")
            scope_mask = first_date.notna() & second_date.notna()
            error_mask = scope_mask & (first_date > second_date)
            error_type = "一致性"

        elif check == "相同":
            scope_mask = ~empty & ~other_empty
            error_mask = scope_mask & (values != other_values)
            error_type = "一致性"

        elif check == "条件必填":
            scope_mask = ~other_empty
            error_mask = scope_mask & empty
            error_type = "一致性"

        elif check == "至少一个非空":
            scope_mask = all_records
            error_mask = empty & other_empty
            error_type = "完整性"

    else:
        raise ValueError(f"没有这种检查方式：{check}")

    error_reason = rule["规则说明"]
    return scope_mask, error_mask, error_type, error_reason


# 依次执行规则，并把结果整理成错误明细和汇总表。
def collect_errors(table, rules):
    """逐条执行业务规则，整理错误明细和规则统计。

    Parameters
    ----------
    table : pandas.DataFrame
        待审查的数据表。
    rules : pandas.DataFrame
        已启用的业务规则。每行规则会传给 check_rule 执行。

    Returns
    -------
    tuple of pandas.DataFrame
        第一个表为错误明细。原始记录会附加源索引、规则编号、
        错误类型、错误原因和规则依据；同一记录违反多条规则时
        会在明细中出现多行。
        第二个表只汇总发现错误的规则，包含总记录数、适用记录数、
        错误记录数以及两种错误率。
    """
    invalid_tables = []
    summary_rows = []
    total_records = len(table)

    # 每条规则单独检查，方便在汇总表中按规则统计。
    for index in rules.index:
        rule = rules.loc[index]
        result = check_rule(table, rule)
        scope_mask, error_mask, error_type, error_reason = result

        error_records = error_mask.sum()

        # 没有错误的规则不写入错误明细和汇总表。
        if error_records > 0:
            invalid = table.loc[error_mask].copy()
            invalid["源索引"] = invalid.index
            invalid["规则编号"] = rule["规则编号"]
            invalid["错误类型"] = error_type
            invalid["错误原因"] = error_reason
            invalid["规则依据"] = rule["规则依据"]
            invalid_tables.append(invalid)

            applicable_records = scope_mask.sum()
            total_error_rate = round(error_records / total_records * 100, 4)
            applicable_error_rate = round(
                error_records / applicable_records * 100,
                4
            )

            summary_rows.append({
                "规则编号": rule["规则编号"],
                "错误类型": error_type,
                "业务规则": error_reason,
                "规则依据": rule["规则依据"],
                "总记录数": total_records,
                "适用记录数": applicable_records,
                "错误记录数": error_records,
                "全表错误率_pct": total_error_rate,
                "适用范围错误率_pct": applicable_error_rate
            })

    if len(invalid_tables) == 0:
        invalid_records = table.iloc[0:0].copy()
    else:
        # 每条规则产生一张错误子表，这里合并成一张明细表。
        invalid_records = pd.concat(invalid_tables, ignore_index=True)

    error_summary = pd.DataFrame(summary_rows)
    return invalid_records, error_summary


# 根据错误明细修改副本，并在同一流程中完成字段映射。
def clean_work_order(
    table,
    invalid_records,
    handling_rules,
    mapping_rules,
    business_rules
):
    """按 Excel 中已启用的规则清洗工单表。

    原表不会被修改。函数返回清洗后的副本和需要人工处理的隔离记录。
    """
    cleaned = table.copy()
    removed_rows = []
    isolated_tables = []
    handled_cells = {}

    # invalid_records 已经保存了每条业务规则对应的错误行。
    active_rules = handling_rules.loc[handling_rules["是否启用"] == "是"]

    for index in active_rules.index:
        rule = active_rules.loc[index]
        rule_id = rule["规则编号"]
        field = rule["错误字段"]
        condition = rule["处理条件"]
        action = rule["处理方式"]
        target = rule["处理字段"]
        value = rule["处理值"]

        rows = invalid_records.loc[
            invalid_records["规则编号"] == rule_id,
            "源索引"
        ].unique()
        rows = pd.Index(rows)

        # 重复主键分为整行重复和同一主键内容冲突。
        if condition == "完全重复":
            rows = rows.intersection(table.index[table.duplicated(keep="first")])

        elif condition == "主键冲突":
            conflict_rows = []
            for key, group in table.loc[rows].groupby(field, dropna=False):
                if len(group.drop_duplicates()) > 1:
                    conflict_rows.extend(group.index)
            rows = pd.Index(conflict_rows)

        # 大小写或多余空格不同的文本可以映射回合法值。
        elif condition in ["可标准化", "无法标准化"]:
            business_rule = business_rules.loc[
                business_rules["规则编号"] == rule_id
            ].iloc[0]
            allowed = str(business_rule["允许值"]).split("|")
            allowed_map = {
                " ".join(item.split()).casefold(): item
                for item in allowed
            }
            values = table.loc[rows, field]
            normalized = values.astype(str).str.split().str.join(" ").str.casefold()
            can_map = normalized.isin(allowed_map)
            rows = values.index[can_map if condition == "可标准化" else ~can_map]

        if len(rows) == 0:
            continue

        # 同一字段要求不同处理时不擅自选择，直接隔离该记录。
        if action not in ["删除记录", "去重", "隔离记录"]:
            rows_to_apply = []
            action_value = value if action in ["填充固定值", "使用对照字段"] else ""

            for row in rows:
                cell = (row, target)
                current_action = (action, action_value)

                if cell not in handled_cells:
                    handled_cells[cell] = current_action
                    rows_to_apply.append(row)
                elif handled_cells[cell] != current_action:
                    conflict = table.loc[[row]].copy()
                    conflict["隔离原因"] = f"字段 {target} 的处理方式冲突"
                    isolated_tables.append(conflict)
                    removed_rows.append(row)

            rows = pd.Index(rows_to_apply)

        if action in ["删除记录", "去重"]:
            removed_rows.extend(rows)
        elif action == "隔离记录":
            isolated = table.loc[rows].copy()
            isolated["隔离原因"] = rule["处理依据"]
            isolated_tables.append(isolated)
            removed_rows.extend(rows)
        elif action == "置为空值":
            cleaned.loc[rows, target] = pd.NA
        elif action == "填充固定值":
            cleaned.loc[rows, target] = value
        elif action == "使用对照字段":
            cleaned.loc[rows, target] = cleaned.loc[rows, value]
        elif action == "标准化映射":
            values = cleaned.loc[rows, target]
            normalized = values.astype(str).str.split().str.join(" ").str.casefold()
            cleaned.loc[rows, target] = normalized.map(allowed_map)
        elif action == "保留并标记":
            cleaned.loc[rows, "处理标记"] = rule_id

    # 字段映射按优先级执行，只填充目标字段中的空值。
    active_mappings = mapping_rules.loc[
        mapping_rules["是否启用"] == "是"
    ].sort_values("优先级")

    for index in active_mappings.index:
        rule = active_mappings.loc[index]
        source = rule["来源字段"]
        target = rule["目标字段"]
        values = cleaned[source]

        if target not in cleaned.columns:
            cleaned[target] = pd.NA

        if rule["匹配方式"] == "等于":
            mask = values == rule["来源值"]
        else:
            not_empty = values.fillna("").astype(str).str.strip() != ""
            mask = not_empty & (values != rule["来源值"])

        mask = mask & cleaned[target].isna()
        cleaned.loc[mask, target] = rule["映射值"]

    cleaned = cleaned.drop(index=pd.Index(removed_rows).unique())

    # 异常值处理完以后，再转换清洗结果的数据类型。
    date_fields = [
        "created_date", "closed_date", "due_date",
        "resolution_action_updated_date"
    ]
    for field in date_fields:
        cleaned[field] = pd.to_datetime(cleaned[field], errors="coerce")

    # 用分钟保存耗时，几分钟和跨天工单都能直接统计。
    cleaned["closure_duration_minutes"] = (
        (cleaned["closed_date"] - cleaned["created_date"])
        .dt.total_seconds()
        .div(60)
        .round(2)
    )
    for field in cleaned.select_dtypes(include="object").columns:
        cleaned[field] = cleaned[field].astype("string")

    category_fields = [
        "agency", "status", "open_data_channel_type", "location_type",
        "address_type", "borough", "facility_type", "vehicle_type",
        "road_ramp", "agency_name_standard"
    ]
    for field in category_fields:
        cleaned[field] = cleaned[field].astype("category")

    if len(isolated_tables) == 0:
        isolated_records = table.iloc[0:0].copy()
    else:
        isolated_records = pd.concat(isolated_tables, ignore_index=True)

    return cleaned, isolated_records


# 将清洗结果拆成主表、机构表和补充字段表。
def split_work_order(table):
    """按分析用途拆分工单表，三张表可通过 unique_key 或 agency 连接。"""
    main_fields = [
        "unique_key", "created_date", "closed_date", "agency",
        "complaint_type", "descriptor", "status",
        "resolution_action_updated_date", "open_data_channel_type",
        "location_type", "borough",
        "closure_duration_minutes"
    ]
    supplement_fields = [
        "unique_key", "agency_name", "descriptor_2", "due_date",
        "address_type", "city", "facility_type", "park_facility_name",
        "park_borough", "vehicle_type", "taxi_company_borough",
        "bridge_highway_name", "road_ramp"
    ]

    fact_work_order = table.loc[:, main_fields].copy()
    dim_agency = table.loc[
        :, ["agency", "agency_name_standard"]
    ].drop_duplicates().reset_index(drop=True)
    work_order_supplement = table.loc[:, supplement_fields].copy()

    return fact_work_order, dim_agency, work_order_supplement


def load_partition_to_mysql(
    file,
    engine,
    first_partition,
    field_description,
    all_business_rules,
    business_rules,
    handling_rules,
    mapping_rules
):
    """处理一个 Parquet 分片并写入 MySQL，返回本分片的行数记录。"""
    raw = pd.read_parquet(file)
    selected = select_analysis_fields(raw, field_description)
    # 只用于检查未来时间，不写入最终业务表。
    selected["_data_snapshot_date"] = DATA_SNAPSHOT_DATE

    rule_errors = validate_business_rules(
        all_business_rules,
        selected,
        "work_order"
    )
    if len(rule_errors) > 0:
        print(rule_errors.to_string(index=False))
        raise ValueError("业务规则表存在无法执行的规则")

    invalid, quality_summary = collect_errors(selected, business_rules)
    selected = selected.drop(columns=["_data_snapshot_date"])
    invalid = invalid.drop(columns=["_data_snapshot_date"])
    cleaned, isolated = clean_work_order(
        selected,
        invalid,
        handling_rules,
        mapping_rules,
        business_rules
    )
    fact, agency, supplement = split_work_order(cleaned)

    write_mode = "replace" if first_partition else "append"
    fact.to_sql(
        "fact_work_order",
        engine,
        if_exists=write_mode,
        index=False,
        chunksize=1000
    )
    supplement.to_sql(
        "work_order_supplement",
        engine,
        if_exists=write_mode,
        index=False,
        chunksize=1000
    )

    if first_partition:
        agency.to_sql("dim_agency", engine, if_exists="replace", index=False)
    else:
        saved_agencies = pd.read_sql("SELECT agency FROM dim_agency", engine)
        new_agencies = agency.loc[
            ~agency["agency"].isin(saved_agencies["agency"])
        ]
        if len(new_agencies) > 0:
            new_agencies.to_sql(
                "dim_agency",
                engine,
                if_exists="append",
                index=False
            )

    invalid = invalid.copy()
    invalid["source_file"] = Path(file).name
    quality_summary = quality_summary.copy()
    quality_summary["source_file"] = Path(file).name

    if len(invalid) > 0:
        invalid.to_sql(
            "data_quality_errors",
            engine,
            if_exists=write_mode,
            index=False,
            chunksize=500
        )
    if len(quality_summary) > 0:
        quality_summary.to_sql(
            "data_quality_summary",
            engine,
            if_exists=write_mode,
            index=False
        )
    if len(isolated) > 0:
        isolated = isolated.copy()
        isolated["source_file"] = Path(file).name
        isolated.to_sql(
            "isolated_work_order",
            engine,
            if_exists="append",
            index=False,
            chunksize=500
        )

    return {
        "分片": Path(file).name,
        "原始记录数": len(raw),
        "主表写入数": len(fact),
        "错误记录数": len(invalid),
        "隔离记录数": len(isolated)
    }


def connect_mysql():
    """读取连接配置并连接显式指定的目标库；不接受隐式重建项目库。"""
    database = os.environ.get("NYC311_MYSQL_DATABASE", "")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", database):
        raise ValueError("请用 NYC311_MYSQL_DATABASE 显式指定目标库（字母、数字、下划线，最长64字符）")
    if database.lower() in {"mysql", "sys", "information_schema", "performance_schema"}:
        raise ValueError("不能使用MySQL系统数据库")
    config = mysql_settings()
    server_url = (
        f"mysql+pymysql://{quote_plus(config['user'])}:{quote_plus(config['password'])}"
        f"@{config['host']}:{config['port']}"
    )

    server_engine = create_engine(server_url)
    with server_engine.begin() as connection:
        connection.execute(
            text(
                f"CREATE DATABASE IF NOT EXISTS `{database}` "
                "CHARACTER SET utf8mb4"
            )
        )
    server_engine.dispose()

    return create_engine(
        server_url + f"/{database}?charset=utf8mb4"
    )


def create_power_bi_view(engine):
    """创建 Power BI 使用的工单视图，一行对应一张工单。"""
    sql = """
    CREATE OR REPLACE VIEW power_bi_work_orders AS
    SELECT
        w.unique_key,
        w.created_date,
        w.closed_date,
        w.agency,
        d.agency_name_standard,
        w.complaint_type,
        w.descriptor,
        w.status,
        w.resolution_action_updated_date,
        w.open_data_channel_type,
        w.location_type,
        w.borough,
        w.closure_duration_minutes
    FROM fact_work_order AS w
    LEFT JOIN dim_agency AS d ON w.agency = d.agency
    """
    with engine.begin() as connection:
        connection.execute(text(sql))


def create_analysis_tables(engine):
    """执行七个分析问题的 SQL，生成供 Power BI 使用的初步分析表。"""
    sql_text = ANALYSIS_SQL_FILE.read_text(encoding="utf-8")

    # 这份 SQL 不包含存储过程，按分号依次执行即可。
    statements = sql_text.split(";")
    with engine.begin() as connection:
        for statement in statements:
            if statement.strip():
                connection.execute(text(statement))


def run_pipeline(process_all_files=False, replace_existing=False):
    """按时间顺序处理分片；默认测试首个分片，True 时处理全部。"""
    files = sorted(DATA_DIR.glob("*.parquet"))
    if not process_all_files:
        files = files[:1]

    # 所有输入检查在连接数据库及任何DDL之前完成。
    if not files:
        raise FileNotFoundError(f"未找到Parquet输入：{DATA_DIR}；数据库未连接")
    for required in [FIELD_FILE, RULES_FILE, ANALYSIS_SQL_FILE]:
        if not required.is_file():
            raise FileNotFoundError(f"缺少配置或SQL文件：{required}")
    required_columns = {"unique_key", "created_date", "complaint_type", "agency"}
    for file in files:
        metadata = pq.ParquetFile(file)
        if metadata.metadata.num_rows == 0:
            raise ValueError(f"Parquet分片为空：{file}")
        missing = required_columns - set(metadata.schema_arrow.names)
        if missing:
            raise ValueError(f"Parquet缺少关键字段：{file.name} {sorted(missing)}")

    field_description = pd.read_excel(
        FIELD_FILE,
        sheet_name="字段说明"
    )
    all_business_rules = pd.read_excel(
        RULES_FILE,
        sheet_name="业务规则"
    )
    business_rules = all_business_rules.loc[
        (all_business_rules["适用表"] == "work_order")
        & (all_business_rules["是否启用"] == "是")
    ].copy()
    handling_rules = pd.read_excel(
        RULES_FILE,
        sheet_name="错误处理规则",
        keep_default_na=False
    )
    mapping_rules = pd.read_excel(
        RULES_FILE,
        sheet_name="字段映射",
        keep_default_na=False
    )

    engine = connect_mysql()
    results = []

    try:
        # 每次运行都重新生成结果，避免旧数据和本次数据叠加。
        output_tables = [
            "fact_work_order",
            "work_order_supplement",
            "dim_agency",
            "data_quality_errors",
            "data_quality_summary",
            "isolated_work_order",
            "analysis_agency_complaint",
            "analysis_cross_agency_efficiency",
            "analysis_borough_complaint",
            "analysis_complaint_mix",
            "analysis_complaint_efficiency",
            "analysis_duration_profiles",
            "analysis_channel_response"
        ]
        existing = set(inspect(engine).get_table_names()) | set(inspect(engine).get_view_names())
        conflicts = existing & set(output_tables + ["power_bi_work_orders"])
        if conflicts and not replace_existing:
            raise RuntimeError("目标库已有结果，未执行删除。确认重建后使用 --replace-existing：" + ", ".join(sorted(conflicts)))
        with engine.begin() as connection:
            connection.execute(text("DROP VIEW IF EXISTS power_bi_work_orders"))
            for table in output_tables:
                connection.execute(text(f"DROP TABLE IF EXISTS `{table}`"))

        for number, file in enumerate(files):
            print(f"[{number + 1}/{len(files)}] {file.name}")
            result = load_partition_to_mysql(
                file,
                engine,
                first_partition=(number == 0),
                field_description=field_description,
                all_business_rules=all_business_rules,
                business_rules=business_rules,
                handling_rules=handling_rules,
                mapping_rules=mapping_rules
            )
            results.append(result)

        create_power_bi_view(engine)
        print("正在生成 SQL 初步分析结果...")
        create_analysis_tables(engine)
    finally:
        engine.dispose()

    print(pd.DataFrame(results).to_string(index=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--all",
        action="store_true",
        help="处理全部 Parquet 分片；不填写时只处理首个分片"
    )
    parser.add_argument("--replace-existing", action="store_true", help="明确允许删除并重建目标库中的项目结果表")
    args = parser.parse_args()
    run_pipeline(process_all_files=args.all, replace_existing=args.replace_existing)
