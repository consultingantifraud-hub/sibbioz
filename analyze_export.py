"""Analytics module for amoCRM exports."""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd
from zoneinfo import ZoneInfo

import config

logger = logging.getLogger(__name__)


LEADS_SHEET = "Leads_500"
CONTACTS_SHEET = "Contacts_500"
COMPANIES_SHEET = "Companies_500"
TASKS_SHEET = "Tasks_500"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build analytics report from amoCRM export")
    parser.add_argument("--input-xlsx", type=Path, default=config.OUT_XLSX, help="Path to enriched export XLSX")
    parser.add_argument("--out-xlsx", type=Path, default=Path("analytics_report.xlsx"), help="Destination analytics XLSX")
    parser.add_argument("--year", type=int, default=2025, help="Target year for analytics")
    parser.add_argument("--tz", type=str, default="Asia/Almaty", help="Timezone for datetime conversions")
    return parser.parse_args()


def _ensure_datetime(series: pd.Series, tz: ZoneInfo) -> pd.Series:
    dt = pd.to_datetime(series, errors="coerce", utc=True)
    return dt.dt.tz_convert(tz)


def _filter_year(df: pd.DataFrame, column: str, year: int) -> pd.DataFrame:
    if column not in df.columns:
        return df
    mask = df[column].dt.year == year
    return df.loc[mask].copy()


def _group_summary(df: pd.DataFrame, group_col: str, value_col: str = "Сумма сделки", count_col: str = "ID") -> pd.DataFrame:
    if group_col not in df.columns:
        return pd.DataFrame()
    grouped = (
        df.groupby(group_col)
        .agg(Количество=(count_col, "count"), Сумма=(value_col, "sum"))
        .sort_values("Количество", ascending=False)
    )
    total = grouped["Количество"].sum()
    if total:
        grouped["%"] = (grouped["Количество"] / total * 100).round(2)
    return grouped


def _monthly_leads(df: pd.DataFrame, created_col: str) -> pd.DataFrame:
    if created_col not in df.columns:
        return pd.DataFrame()
    tmp = df.set_index(created_col)
    monthly = tmp.resample("M").agg({"ID": "count", "Сумма сделки": "sum"})
    if monthly.empty:
        return monthly
    monthly.rename(columns={"ID": "Сделок", "Сумма сделки": "Сумма"}, inplace=True)
    denom = monthly["Сделок"].replace(0, pd.NA).astype("float64")
    monthly["Средний бюджет"] = (monthly["Сумма"].astype("float64") / denom).round(2)
    monthly.index = monthly.index.to_period("M").astype(str)
    return monthly


def _pivot_month(df: pd.DataFrame, column: str, created_col: str) -> pd.DataFrame:
    if column not in df.columns or created_col not in df.columns:
        return pd.DataFrame()
    idx = df[created_col].dt.to_period("M")
    pivot = pd.pivot_table(df, index=idx, columns=column, values="ID", aggfunc="count", fill_value=0)
    pivot.index = pivot.index.astype(str)
    return pivot


def _age_days(df: pd.DataFrame, created_col: str, closed_col: str, tz: ZoneInfo) -> pd.Series:
    created = df.get(created_col)
    if created is None:
        return pd.Series(dtype="float64")
    created = pd.to_datetime(created, errors="coerce", utc=True).dt.tz_convert(tz)
    closed = df.get(closed_col)
    closed_ts = pd.to_datetime(closed, errors="coerce", utc=True).dt.tz_convert(tz) if closed_col in df else None
    now = pd.Timestamp.now(tz)
    if closed_ts is not None:
        end = closed_ts.fillna(now)
    else:
        end = pd.Series(now, index=created.index)
    delta = (end - created).dt.total_seconds() / 86400
    return delta


def _load_sheet(xls: pd.ExcelFile, sheet_name: str) -> Optional[pd.DataFrame]:
    if sheet_name not in xls.sheet_names:
        logger.warning("Пропускаю отсутствующий лист %s", sheet_name)
        return None
    return xls.parse(sheet_name)


def _print_top(table: pd.DataFrame, label: str, top_n: int = 5) -> None:
    if table is None or table.empty:
        return
    print(table.head(top_n).to_string())


def build_leads_overview(df: pd.DataFrame, tz: ZoneInfo, year: int) -> Tuple[List[Tuple[str, pd.DataFrame]], int]:
    if df.empty:
        return [], 0
    for col in ("Создано (дата/время)", "Обновлено (дата/время)", "Закрыто (дата/время)"):
        if col in df.columns:
            df[col] = _ensure_datetime(df[col], tz)
    df = _filter_year(df, "Создано (дата/время)", year)
    if df.empty:
        return [], 0

    total_deals = int(df.shape[0])
    total_budget = float(df.get("Сумма сделки", pd.Series(dtype="float64")).sum())
    avg_budget = float(df.get("Сумма сделки", pd.Series(dtype="float64")).mean() or 0)
    median_budget = float(df.get("Сумма сделки", pd.Series(dtype="float64")).median() or 0)
    ages = _age_days(df, "Создано (дата/время)", "Закрыто (дата/время)", tz)
    avg_age = float(ages.mean() or 0)

    summary = pd.DataFrame(
        {
            "Показатель": [
                "Сделок",
                "Сумма бюджета",
                "Средний бюджет",
                "Медианный бюджет",
                "Средний возраст (дни)",
            ],
            "Значение": [
                total_deals,
                round(total_budget, 2),
                round(avg_budget, 2),
                round(median_budget, 2),
                round(avg_age, 2),
            ],
        }
    )

    pipelines = _group_summary(df, "Воронка")
    statuses = _group_summary(df, "Статус")
    responsible = _group_summary(df, "Ответственный")
    loss_reasons = _group_summary(df[df.get("Причина потери").notna()], "Причина потери") if "Причина потери" in df.columns else pd.DataFrame()

    monthly = _monthly_leads(df, "Создано (дата/время)")
    pivot_pipeline = _pivot_month(df, "Воронка", "Создано (дата/время)")
    pivot_status = _pivot_month(df, "Статус", "Создано (дата/время)")

    tables = [
        ("Summary", summary),
        ("By pipeline", pipelines),
        ("By status", statuses),
        ("By responsible", responsible),
    ]
    if loss_reasons is not None and not loss_reasons.empty:
        tables.append(("Loss reasons", loss_reasons))
    if monthly is not None and not monthly.empty:
        tables.append(("Monthly totals", monthly))
    if pivot_pipeline is not None and not pivot_pipeline.empty:
        tables.append(("Monthly by pipeline", pivot_pipeline))
    if pivot_status is not None and not pivot_status.empty:
        tables.append(("Monthly by status", pivot_status))

    print(f"[Leads_Overview] строк: {total_deals}, бюджет: {round(total_budget, 2)}")
    _print_top(pipelines, "Воронки")

    return tables, total_deals


def build_leads_by_month(df: pd.DataFrame, tz: ZoneInfo, year: int) -> List[Tuple[str, pd.DataFrame]]:
    if df.empty:
        return []
    df = df.copy()
    df["Создано (дата/время)"] = _ensure_datetime(df["Создано (дата/время)"], tz)
    df = _filter_year(df, "Создано (дата/время)", year)
    if df.empty:
        return []
    monthly = _monthly_leads(df, "Создано (дата/время)")
    pipeline = _pivot_month(df, "Воронка", "Создано (дата/время)")
    status = _pivot_month(df, "Статус", "Создано (дата/время)")
    tables: List[Tuple[str, pd.DataFrame]] = []
    if monthly is not None and not monthly.empty:
        tables.append(("Monthly summary", monthly))
    if pipeline is not None and not pipeline.empty:
        tables.append(("Pipeline by month", pipeline))
    if status is not None and not status.empty:
        tables.append(("Status by month", status))
    return tables


def _tasks_metrics(df: pd.DataFrame, tz: ZoneInfo) -> List[Tuple[str, pd.DataFrame]]:
    if df.empty:
        return []
    df = df.copy()
    for col in ("Создано (Алматы)", "Срок (Алматы)"):
        if col in df.columns:
            df[col] = _ensure_datetime(df[col], tz)
    now = pd.Timestamp.now(tz)
    if "Срок (Алматы)" in df.columns and "Создано (Алматы)" in df.columns:
        df["Часы до срока"] = (df["Срок (Алматы)"] - df["Создано (Алматы)"]).dt.total_seconds() / 3600
    else:
        df["Часы до срока"] = pd.Series(pd.NA, index=df.index, dtype="float64")
    df["Просрочена"] = False
    if "Срок (Алматы)" in df.columns:
        due = df["Срок (Алматы)"]
        df["Просрочена"] = due.notna() & (due < now) & ~df.get("Выполнена", pd.Series(False, index=df.index)).astype(bool)

    def _aggregate(group_col: str) -> pd.DataFrame:
        if group_col not in df.columns:
            return pd.DataFrame()
        grouped = df.groupby(group_col).agg(
            {
                "ID задачи": "count",
                "Выполнена": "mean",
                "Часы до срока": "mean",
                "Просрочена": "mean",
            }
        )
        grouped.rename(
            columns={
                "ID задачи": "Количество",
                "Выполнена": "Выполнено",
                "Часы до срока": "Среднее время до срока (ч)",
                "Просрочена": "Просрочено %",
            },
            inplace=True,
        )
        grouped["Выполнено"] = (grouped["Выполнено"] * 100).round(1)
        grouped["Просрочено %"] = (grouped["Просрочено %"] * 100).round(1)
        grouped["Среднее время до срока (ч)"] = grouped["Среднее время до срока (ч)"].round(2)
        return grouped.sort_values("Количество", ascending=False)

    by_type = _aggregate("Тип задачи")
    by_resp = _aggregate("Ответственный (ID)")
    print(f"[Tasks_Overview] строк: {len(df)}")
    _print_top(by_type, "Типы задач")
    tables: List[Tuple[str, pd.DataFrame]] = []
    if by_type is not None and not by_type.empty:
        tables.append(("By task type", by_type))
    if by_resp is not None and not by_resp.empty:
        tables.append(("By responsible", by_resp))
    return tables


def _contacts_companies_metrics(contacts: Optional[pd.DataFrame], companies: Optional[pd.DataFrame]) -> List[Tuple[str, pd.DataFrame]]:
    def _analyze(df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame()
        lowered = [c for c in df.columns if isinstance(c, str)]
        phone_cols = [c for c in lowered if "телефон" in c.lower()]
        email_cols = [c for c in lowered if "email" in c.lower() or "e-mail" in c.lower()]
        result_rows = []
        total = len(df)
        for cols, label in ((phone_cols, "Телефоны"), (email_cols, "Email")):
            if not cols:
                continue
            subset = df[cols].replace({"": pd.NA})
            counts = subset.notna().sum(axis=1)
            share = (counts > 0).mean() * 100
            distribution = {
                "1": int((counts == 1).sum()),
                "2": int((counts == 2).sum()),
                "3+": int((counts >= 3).sum()),
            }
            result_rows.append(
                {
                    "Показатель": f"{label}: >=1",
                    "Значение": round(share, 2),
                    "Комментарий": f"из {total} записей",
                }
            )
            for key, value in distribution.items():
                result_rows.append(
                    {
                        "Показатель": f"{label}: {key}",
                        "Значение": value,
                        "Комментарий": "строк",
                    }
                )
        return pd.DataFrame(result_rows)

    tables: List[Tuple[str, pd.DataFrame]] = []
    contacts_table = _analyze(contacts) if contacts is not None else pd.DataFrame()
    companies_table = _analyze(companies) if companies is not None else pd.DataFrame()
    if contacts_table is not None and not contacts_table.empty:
        tables.append(("Contacts", contacts_table))
        print(f"[Contacts_Companies_Overview] контактов: {len(contacts) if contacts is not None else 0}")
    if companies_table is not None and not companies_table.empty:
        tables.append(("Companies", companies_table))
        print(f"[Contacts_Companies_Overview] компаний: {len(companies) if companies is not None else 0}")
    return tables


def write_tables(writer: pd.ExcelWriter, sheet_name: str, tables: List[Tuple[str, pd.DataFrame]]) -> None:
    if not tables:
        return
    workbook = writer.book
    worksheet = workbook.add_worksheet(sheet_name)
    writer.sheets[sheet_name] = worksheet
    row = 0
    for title, table in tables:
        if title:
            worksheet.write(row, 0, title)
            row += 1
        if table is not None and not table.empty:
            table.to_excel(writer, sheet_name=sheet_name, startrow=row, index=True)
            row += len(table.index) + 3
        else:
            row += 2


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    input_path = Path(args.input_xlsx)
    if not input_path.exists():
        raise FileNotFoundError(f"Не найден входной файл: {input_path}")

    tz = ZoneInfo(args.tz)
    xls = pd.ExcelFile(input_path)

    leads_df = _load_sheet(xls, LEADS_SHEET)
    contacts_df = _load_sheet(xls, CONTACTS_SHEET)
    companies_df = _load_sheet(xls, COMPANIES_SHEET)
    tasks_df = _load_sheet(xls, TASKS_SHEET)

    with pd.ExcelWriter(args.out_xlsx, engine="xlsxwriter") as writer:
        if leads_df is not None:
            overview_tables, _ = build_leads_overview(leads_df, tz, args.year)
            write_tables(writer, "Leads_Overview", overview_tables)
            monthly_tables = build_leads_by_month(leads_df, tz, args.year)
            write_tables(writer, "Leads_By_Month", monthly_tables)
        if tasks_df is not None:
            task_tables = _tasks_metrics(tasks_df, tz)
            write_tables(writer, "Tasks_Overview", task_tables)
        contact_tables = _contacts_companies_metrics(contacts_df, companies_df)
        write_tables(writer, "Contacts_Companies_Overview", contact_tables)

    print(f"Готово → {args.out_xlsx}")
if __name__ == "__main__":
    main()
