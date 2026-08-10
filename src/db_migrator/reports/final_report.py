from __future__ import annotations

import csv
import json
from dataclasses import asdict
from html import escape
from pathlib import Path

from db_migrator.core.validation import ValidationReport, ValidationStatus


def write_validation_report(report: ValidationReport, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(report, output_dir / "summary.json")
    _write_csv(report, output_dir / "tables.csv")
    _write_html(report, output_dir / "summary.html")
    _write_errors(report, output_dir / "errors.csv")
    _write_differences(report, output_dir / "differences.csv")


def _write_json(report: ValidationReport, output_path: Path) -> None:
    output_path.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(report: ValidationReport, output_path: Path) -> None:
    with output_path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(
            [
                "schema",
                "table",
                "status",
                "source_rows",
                "target_rows",
                "row_count_status",
                "checksum_status",
                "source_checksum",
                "target_checksum",
            ]
        )
        for table in report.tables:
            writer.writerow(
                [
                    table.table.schema,
                    table.table.name,
                    table.status,
                    table.row_count.source_rows,
                    table.row_count.target_rows,
                    table.row_count.status,
                    table.checksum.status,
                    table.checksum.source_checksum,
                    table.checksum.target_checksum,
                ]
            )


def _write_html(report: ValidationReport, output_path: Path) -> None:
    issue_rows = "\n".join(_issue_rows(table) for table in report.tables if table.status != ValidationStatus.MATCHED)
    if not issue_rows:
        issue_rows = """
          <tr>
            <td colspan="6" class="empty-state">검증 이슈가 없습니다. Source와 target이 일치합니다.</td>
          </tr>
        """
    matched_sample_rows = "\n".join(_matched_sample_row(table) for table in report.tables if table.checksum.matched_samples)
    if not matched_sample_rows:
        matched_sample_rows = """
          <tr>
            <td colspan="4" class="empty-state">표시할 정상 이관 샘플이 없습니다.</td>
          </tr>
        """
    html = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>DB Migrator 검증 리포트</title>
  <style>
    :root {{
      color-scheme: light;
      --color-canvas: oklch(1.000 0.000 0);
      --color-canvas-200: oklch(0.976 0.000 0);
      --color-primary: oklch(0.000 0.000 0);
      --color-text-strong: oklch(0.269 0.000 0);
      --color-text-normal: oklch(0.417 0.000 0);
      --color-text-muted: oklch(0.478 0.000 0);
      --color-border: oklch(0.827 0.000 0);
      --color-success-soft: oklch(0.974 0.016 167);
      --color-success-text: oklch(0.407 0.090 162);
      --color-warning-soft: oklch(0.979 0.012 51);
      --color-warning-text: oklch(0.503 0.188 33);
      --color-warning-border: oklch(0.836 0.092 46);
      --color-danger-soft: oklch(0.978 0.011 24);
      --color-danger-text: oklch(0.505 0.196 24);
      --color-danger-border: oklch(0.838 0.089 20);
      --color-code-bg: oklch(0.269 0.000 0);
      --space-050: 4px;
      --space-100: 8px;
      --space-150: 12px;
      --space-200: 16px;
      --space-300: 24px;
      --space-400: 32px;
      --radius-200: 6px;
      --radius-300: 8px;
      --radius-400: 12px;
      --radius-circle: 9999px;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--color-canvas-200);
      color: var(--color-text-strong);
      font-family: Pretendard, "Segoe UI", Arial, sans-serif;
      line-height: 1.5;
    }}
    main {{
      width: min(1180px, calc(100% - 48px));
      margin: var(--space-400) auto 48px;
    }}
    header {{
      display: grid;
      gap: var(--space-200);
      margin-bottom: var(--space-300);
    }}
    h1 {{
      margin: 0;
      font-size: 32px;
      line-height: 40px;
      font-weight: 800;
      letter-spacing: 0;
    }}
    h2 {{
      margin: 0 0 var(--space-200);
      font-size: 18px;
      line-height: 26px;
      font-weight: 700;
      letter-spacing: 0;
    }}
    p {{
      margin: 0;
      color: var(--color-text-muted);
      font-size: 14px;
      line-height: 20px;
    }}
    .eyebrow {{
      margin-bottom: var(--space-050);
      color: var(--color-text-muted);
      font-size: 12px;
      font-weight: 600;
      line-height: 18px;
      text-transform: uppercase;
    }}
    .summary-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: var(--space-150);
    }}
    .metric {{
      background: var(--color-canvas);
      border: 1px solid var(--color-border);
      border-radius: var(--radius-400);
      padding: var(--space-200);
    }}
    .metric span {{
      display: block;
      color: var(--color-text-muted);
      font-size: 13px;
      line-height: 20px;
      font-weight: 500;
      margin-bottom: 6px;
    }}
    .metric strong {{
      display: block;
      color: var(--color-primary);
      font-size: 32px;
      line-height: 40px;
      font-weight: 800;
    }}
    .context-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: var(--space-150);
    }}
    .context-block {{
      border: 1px solid var(--color-border);
      border-radius: var(--radius-300);
      overflow: hidden;
    }}
    .context-block h3 {{
      margin: 0;
      padding: var(--space-150) var(--space-200);
      background: var(--color-canvas-200);
      color: var(--color-text-strong);
      font-size: 14px;
      line-height: 20px;
      font-weight: 700;
    }}
    .context-list {{
      display: grid;
      grid-template-columns: 120px 1fr;
      margin: 0;
    }}
    .context-list dt,
    .context-list dd {{
      margin: 0;
      padding: var(--space-100) var(--space-150);
      border-top: 1px solid var(--color-border);
      font-size: 13px;
      line-height: 19px;
      word-break: break-word;
    }}
    .context-list dt {{
      color: var(--color-text-muted);
      font-weight: 700;
    }}
    .context-list dd {{
      color: var(--color-text-normal);
    }}
    section {{
      background: var(--color-canvas);
      border: 1px solid var(--color-border);
      border-radius: var(--radius-400);
      padding: var(--space-300);
      margin-top: var(--space-200);
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }}
    th, td {{
      padding: var(--space-150) var(--space-200);
      border-bottom: 1px solid var(--color-border);
      vertical-align: top;
      text-align: left;
      word-break: break-word;
      color: var(--color-text-normal);
      font-size: 14px;
      line-height: 20px;
    }}
    th {{
      background: var(--color-canvas-200);
      color: var(--color-text-muted);
      font-size: 13px;
      font-weight: 700;
      text-transform: none;
    }}
    tr:last-child td {{
      border-bottom: 0;
    }}
    tbody tr:hover td {{
      background: var(--color-canvas-200);
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      min-height: 22px;
      padding: 0 var(--space-100);
      border-radius: var(--radius-circle);
      font-size: 12px;
      line-height: 18px;
      font-weight: 600;
      background: var(--color-primary);
      color: var(--color-canvas);
    }}
    .badge.matched {{
      background: var(--color-success-soft);
      color: var(--color-success-text);
    }}
    .badge.mismatched, .badge.skipped {{
      background: var(--color-warning-soft);
      color: var(--color-warning-text);
      border: 1px solid var(--color-warning-border);
    }}
    .badge.failed {{
      background: var(--color-danger-soft);
      color: var(--color-danger-text);
      border: 1px solid var(--color-danger-border);
    }}
    .message {{
      font-weight: 600;
      color: var(--color-text-strong);
    }}
    .action {{
      color: var(--color-text-muted);
    }}
    .empty-state {{
      text-align: center;
      color: var(--color-success-text);
      padding: var(--space-300);
    }}
    code {{
      display: inline-block;
      max-width: 100%;
      overflow-x: auto;
      padding: 2px 6px;
      border-radius: var(--radius-200);
      background: var(--color-canvas-200);
      color: var(--color-text-strong);
      font-size: 12px;
      line-height: 18px;
    }}
    .value-cell {{
      font-family: "Segoe UI", Arial, sans-serif;
      white-space: pre-wrap;
    }}
    details.issue-details {{
      width: 100%;
    }}
    .issue-summary {{
      display: grid;
      grid-template-columns: 14% 18% 14% 14% 20% 20%;
      align-items: start;
      min-height: 48px;
      cursor: pointer;
      list-style: none;
    }}
    .issue-summary::-webkit-details-marker {{
      display: none;
    }}
    .issue-summary > span {{
      padding: var(--space-150) var(--space-200);
      color: var(--color-text-normal);
      font-size: 14px;
      line-height: 20px;
      word-break: break-word;
    }}
    .issue-summary > .summary-table {{
      cursor: pointer;
      color: var(--color-primary);
      font-weight: 700;
      outline: none;
    }}
    .issue-summary:hover > span {{
      background: var(--color-canvas-200);
    }}
    .issue-summary:hover > .summary-table {{
      color: var(--color-text-normal);
    }}
    .issue-summary:focus-visible {{
      border-radius: var(--radius-200);
      outline: 2px solid var(--color-primary);
      outline-offset: 2px;
    }}
    .difference-panel {{
      margin: 0 var(--space-200) var(--space-200);
      border: 1px solid var(--color-border);
      border-radius: var(--radius-300);
      overflow: hidden;
    }}
    .difference-panel table {{
      table-layout: fixed;
    }}
    .difference-panel th,
    .difference-panel td {{
      padding: var(--space-100) var(--space-150);
      font-size: 13px;
      line-height: 19px;
    }}
    .matched-summary {{
      display: grid;
      grid-template-columns: 16% 24% 18% 42%;
      align-items: start;
      min-height: 48px;
      cursor: pointer;
      list-style: none;
    }}
    .matched-summary::-webkit-details-marker {{
      display: none;
    }}
    .matched-summary > span {{
      padding: var(--space-150) var(--space-200);
      color: var(--color-text-normal);
      font-size: 14px;
      line-height: 20px;
      word-break: break-word;
    }}
    .matched-summary > .summary-table {{
      color: var(--color-primary);
      font-weight: 700;
    }}
    .matched-summary:hover > span {{
      background: var(--color-canvas-200);
    }}
    .matched-panel {{
      margin: 0 var(--space-200) var(--space-200);
      border: 1px solid var(--color-border);
      border-radius: var(--radius-300);
      overflow: hidden;
    }}
    .matched-panel th,
    .matched-panel td {{
      padding: var(--space-100) var(--space-150);
      font-size: 13px;
      line-height: 19px;
    }}
    .row-json {{
      margin: 0;
      max-height: 220px;
      overflow: auto;
      white-space: pre-wrap;
      font-family: Consolas, "Courier New", monospace;
      font-size: 12px;
      line-height: 18px;
      color: var(--color-text-strong);
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <div class="eyebrow">검증 리포트</div>
        <h1>DB Migrator 검증 리포트</h1>
        <p>Source와 target의 행 수와 checksum 샘플 결과를 검토하는 리포트입니다.</p>
      </div>
      <div class="summary-grid">
        <div class="metric"><span>작업 ID</span><strong>{escape(report.job_id)}</strong></div>
        <div class="metric"><span>전체 상태</span><strong>{escape(_status_label(report.status))}</strong></div>
        <div class="metric"><span>총 테이블 수</span><strong>{len(report.tables)}</strong></div>
        <div class="metric"><span>이슈 수</span><strong>{_issue_count(report)}</strong></div>
      </div>
    </header>

    <section>
      <h2>검증 대상 및 기준</h2>
      <div class="context-grid">
        {_endpoint_block("원본 DB", report.metadata.source)}
        {_endpoint_block("대상 DB", report.metadata.target)}
        {_verification_block(report)}
      </div>
    </section>

    <section>
      <h2>이슈 및 권장 조치</h2>
      <p>문제가 있는 테이블만 표시합니다. Checksum 불일치는 테이블명을 클릭하면 전체 폭으로 값 차이를 펼쳐 볼 수 있습니다.</p>
      <table>
        <thead>
          <tr>
            <th style="width: 14%;">스키마</th>
            <th style="width: 18%;">테이블명</th>
            <th style="width: 14%;">검증 항목</th>
            <th style="width: 14%;">상태</th>
            <th style="width: 20%;">상세</th>
            <th style="width: 20%;">권장 조치</th>
          </tr>
        </thead>
        <tbody>
{issue_rows}
        </tbody>
      </table>
    </section>

    <section>
      <h2>정상 이관 샘플</h2>
      <p>테이블별로 정상 매칭된 데이터를 최대 3개 표시합니다. 이관 전과 이관 후가 같은 값으로 정규화되어 들어갔는지 빠르게 확인하는 용도입니다.</p>
      <table>
        <thead>
          <tr>
            <th style="width: 16%;">스키마</th>
            <th style="width: 24%;">테이블명</th>
            <th style="width: 18%;">상태</th>
            <th style="width: 42%;">정상 샘플</th>
          </tr>
        </thead>
        <tbody>
{matched_sample_rows}
        </tbody>
      </table>
    </section>
  </main>
</body>
</html>
"""
    output_path.write_text(html, encoding="utf-8")


def _endpoint_block(title: str, endpoint) -> str:
    if endpoint is None:
        rows = _context_rows(
            [
                ("DBMS", "-"),
                ("호스트", "-"),
                ("데이터베이스", "-"),
                ("스키마", "-"),
            ]
        )
    else:
        rows = _context_rows(
            [
                ("DBMS", endpoint.dbms.upper()),
                ("호스트", f"{endpoint.host}:{endpoint.port}"),
                ("데이터베이스", endpoint.database),
                ("스키마", endpoint.schema or "-"),
            ]
        )
    return f"""
        <div class="context-block">
          <h3>{escape(title)}</h3>
          <dl class="context-list">
{rows}
          </dl>
        </div>
    """


def _verification_block(report: ValidationReport) -> str:
    counts = _status_counts(report)
    rows = _context_rows(
        [
            ("실행 시각", report.metadata.generated_at),
            ("샘플 크기", _format_optional_int(report.metadata.checksum_sample_size)),
            ("Timezone 기준", report.metadata.checksum_timezone or "-"),
            ("Datetime 정밀도", report.metadata.checksum_datetime_precision or "-"),
            ("결과 요약", _status_summary(counts)),
        ]
    )
    return f"""
        <div class="context-block">
          <h3>검증 기준</h3>
          <dl class="context-list">
{rows}
          </dl>
        </div>
    """


def _context_rows(rows: list[tuple[str, str]]) -> str:
    return "\n".join(
        f"""
            <dt>{escape(label)}</dt>
            <dd>{escape(value)}</dd>
        """
        for label, value in rows
    )


def _status_counts(report: ValidationReport) -> dict[str, int]:
    return {
        ValidationStatus.MATCHED: sum(1 for table in report.tables if table.status == ValidationStatus.MATCHED),
        ValidationStatus.MISMATCHED: sum(1 for table in report.tables if table.status == ValidationStatus.MISMATCHED),
        ValidationStatus.FAILED: sum(1 for table in report.tables if table.status == ValidationStatus.FAILED),
        ValidationStatus.SKIPPED: sum(1 for table in report.tables if table.status == ValidationStatus.SKIPPED),
    }


def _status_summary(counts: dict[str, int]) -> str:
    return (
        f"일치 {counts[ValidationStatus.MATCHED]}, "
        f"불일치 {counts[ValidationStatus.MISMATCHED]}, "
        f"실패 {counts[ValidationStatus.FAILED]}, "
        f"건너뜀 {counts[ValidationStatus.SKIPPED]}"
    )


def _write_errors(report: ValidationReport, output_path: Path) -> None:
    with output_path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(["schema", "table", "validation", "status", "message"])
        for table in report.tables:
            if table.row_count.status in {ValidationStatus.MISMATCHED, ValidationStatus.FAILED}:
                writer.writerow([table.table.schema, table.table.name, "row_count", table.row_count.status, table.row_count.message])
            if table.checksum.status in {ValidationStatus.MISMATCHED, ValidationStatus.FAILED}:
                writer.writerow([table.table.schema, table.table.name, "checksum", table.checksum.status, table.checksum.message])


def _write_differences(report: ValidationReport, output_path: Path) -> None:
    with output_path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(["schema", "table", "row_identity", "column", "source_value", "target_value"])
        for table in report.tables:
            for difference in table.checksum.differences:
                writer.writerow(
                    [
                        table.table.schema,
                        table.table.name,
                        difference.row_identity,
                        difference.column,
                        difference.source_value,
                        difference.target_value,
                    ]
                )


def _issue_count(report: ValidationReport) -> int:
    return sum(1 for table in report.tables if table.status != ValidationStatus.MATCHED)


def _issue_rows(table) -> str:
    rows = []
    if table.row_count.status in {ValidationStatus.MISMATCHED, ValidationStatus.FAILED}:
        rows.append(
            _issue_row(
                schema=table.table.schema,
                table=table.table.name,
                validation="row_count",
                status=table.row_count.status,
                detail=_row_count_detail(table),
                action=_row_count_action(table),
            )
        )
    if table.checksum.status in {ValidationStatus.MISMATCHED, ValidationStatus.FAILED}:
        rows.append(
            _issue_row(
                schema=table.table.schema,
                table=table.table.name,
                validation="checksum",
                status=table.checksum.status,
                detail=_checksum_detail(table),
                action=_checksum_action(table),
                table_details=_difference_panel(table) if table.checksum.differences else "",
            )
        )
    return "\n".join(rows)


def _issue_row(
    *,
    schema: str,
    table: str,
    validation: str,
    status: str,
    detail: str,
    action: str,
    table_details: str = "",
) -> str:
    if table_details:
        return _expandable_issue_row(
            schema=schema,
            table=table,
            validation=validation,
            status=status,
            detail=detail,
            action=action,
            table_details=table_details,
        )
    return f"""
          <tr>
            <td>{escape(schema)}</td>
            <td>{escape(table)}</td>
            <td class="message">{escape(_validation_label(validation))}</td>
            <td><span class="badge {escape(status)}">{escape(_status_label(status))}</span></td>
            <td>{detail}</td>
            <td class="action">{escape(action)}</td>
          </tr>
    """


def _expandable_issue_row(
    *,
    schema: str,
    table: str,
    validation: str,
    status: str,
    detail: str,
    action: str,
    table_details: str,
) -> str:
    return f"""
          <tr>
            <td colspan="6">
              <details class="issue-details">
                <summary class="issue-summary">
                  <span>{escape(schema)}</span>
                  <span class="summary-table">{escape(table)}</span>
                  <span class="message">{escape(_validation_label(validation))}</span>
                  <span><span class="badge {escape(status)}">{escape(_status_label(status))}</span></span>
                  <span>{detail}</span>
                  <span class="action">{escape(action)}</span>
                </summary>
                {table_details}
              </details>
            </td>
          </tr>
    """


def _row_count_detail(table) -> str:
    if table.row_count.message:
        return escape(table.row_count.message)
    return (
        f"source 행 수={_format_optional_int(table.row_count.source_rows)} "
        f"target 행 수={_format_optional_int(table.row_count.target_rows)}"
    )


def _checksum_detail(table) -> str:
    if table.checksum.message:
        return escape(table.checksum.message)
    if table.checksum.differences:
        difference_count = len(table.checksum.differences)
        return f"checksum 샘플에서 값 차이 {difference_count}건이 발견되었습니다."
    return (
        f"source checksum=<code>{escape(_short_checksum(table.checksum.source_checksum))}</code> "
        f"target checksum=<code>{escape(_short_checksum(table.checksum.target_checksum))}</code>"
    )


def _row_count_action(table) -> str:
    if table.row_count.status == ValidationStatus.FAILED:
        return "Source/target 행 수 조회 권한을 확인한 뒤 validate를 다시 실행하세요."
    source_rows = table.row_count.source_rows
    target_rows = table.row_count.target_rows
    if source_rows is None or target_rows is None:
        return "Source/target 행 수 조회 결과를 확인한 뒤 validate를 다시 실행하세요."
    if source_rows > target_rows:
        missing_rows = source_rows - target_rows
        return f"Target에 {missing_rows}행이 부족합니다. 해당 테이블을 migrate-data 또는 resume으로 다시 이관한 뒤 validate를 재실행하세요."
    if target_rows > source_rows:
        extra_rows = target_rows - source_rows
        return f"Target에 {extra_rows}행이 더 많습니다. 중복 데이터나 기존 target 잔여 데이터를 확인한 뒤 재시도하세요."
    return "행 수는 일치합니다. checksum 상세에서 값 단위 차이를 확인하세요."


def _checksum_action(table) -> str:
    if table.checksum.status == ValidationStatus.FAILED:
        return "샘플 조회 권한, 지원하지 않는 컬럼 값, 정규화 설정을 확인하세요."
    if not table.checksum.differences:
        return "Checksum은 다르지만 샘플 값에서 컬럼 차이를 찾지 못했습니다. checksum_sample_size를 늘린 뒤 validate를 재실행하세요."

    columns = _difference_columns(table)
    if _has_row_presence_difference(table):
        return f"{columns} 기준 샘플 행 구성이 다릅니다. 기본키와 행 수를 확인한 뒤 해당 테이블을 다시 검증하세요."
    if _has_missing_column_difference(table):
        return f"{columns} 컬럼이 한쪽에 없습니다. SELECT 컬럼 목록, generated column, target DDL을 확인하세요."
    if _has_temporal_difference(table):
        return f"{columns}의 timezone 또는 초 이하 정밀도를 확인하세요. 타입 매핑/정규화 설정을 맞춘 뒤 영향 행을 재이관하세요."
    if _has_numeric_difference(table):
        return f"{columns}의 숫자 정밀도, scale, 반올림 여부를 확인하세요. target 컬럼 타입을 맞춘 뒤 영향 행을 재이관하세요."
    if _has_json_difference(table):
        return f"{columns}의 JSON 직렬화 방식을 확인하세요. json/jsonb 의미 차이와 정규화된 payload를 비교하세요."
    return f"{columns}의 값 변환을 확인하세요. 정규화 후에도 source/target 샘플 값이 다릅니다."


def _format_optional_int(value: int | None) -> str:
    return "-" if value is None else str(value)


def _short_checksum(value: str | None) -> str:
    if value is None:
        return "-"
    return value[:12]


def _status_label(status: str) -> str:
    return {
        ValidationStatus.MATCHED: "일치",
        ValidationStatus.MISMATCHED: "불일치",
        ValidationStatus.SKIPPED: "건너뜀",
        ValidationStatus.FAILED: "실패",
    }.get(status, status)


def _validation_label(validation: str) -> str:
    return {
        "row_count": "행 수",
        "checksum": "Checksum",
    }.get(validation, validation)


def _difference_columns(table, limit: int = 3) -> str:
    columns = []
    for difference in table.checksum.differences:
        if difference.column not in columns:
            columns.append(difference.column)
        if len(columns) >= limit:
            break
    suffix = " 외 추가 컬럼" if len({difference.column for difference in table.checksum.differences}) > limit else ""
    return ", ".join(columns) + suffix


def _has_row_presence_difference(table) -> bool:
    return any(
        difference.column == "<row>"
        or difference.source_value in {"<MISSING_ROW>", "<PRESENT_ROW>"}
        or difference.target_value in {"<MISSING_ROW>", "<PRESENT_ROW>"}
        for difference in table.checksum.differences
    )


def _has_missing_column_difference(table) -> bool:
    return any(
        difference.source_value == "<MISSING>" or difference.target_value == "<MISSING>"
        for difference in table.checksum.differences
    )


def _has_temporal_difference(table) -> bool:
    return any(
        _looks_temporal(difference.column)
        or _looks_temporal(difference.source_value)
        or _looks_temporal(difference.target_value)
        for difference in table.checksum.differences
    )


def _has_numeric_difference(table) -> bool:
    return any(
        _looks_numeric(difference.source_value) and _looks_numeric(difference.target_value)
        for difference in table.checksum.differences
    )


def _has_json_difference(table) -> bool:
    return any(
        _looks_json(difference.source_value) or _looks_json(difference.target_value)
        for difference in table.checksum.differences
    )


def _looks_temporal(value: str) -> bool:
    normalized_value = value.lower()
    return (
        "date" in normalized_value
        or "time" in normalized_value
        or "created_at" in normalized_value
        or "updated_at" in normalized_value
        or "t" in value and ":" in value and "-" in value
    )


def _looks_numeric(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def _looks_json(value: str) -> bool:
    stripped_value = value.strip()
    return (
        (stripped_value.startswith("{") and stripped_value.endswith("}"))
        or (stripped_value.startswith("[") and stripped_value.endswith("]"))
    )


def _difference_panel(table) -> str:
    difference_rows = "\n".join(
        f"""
          <tr>
            <td>{escape(difference.row_identity)}</td>
            <td class="message">{escape(difference.column)}</td>
            <td class="value-cell">{escape(difference.source_value)}</td>
            <td class="value-cell">{escape(difference.target_value)}</td>
          </tr>
        """
        for difference in table.checksum.differences
    )
    return f"""
      <div class="difference-panel">
        <table>
          <thead>
            <tr>
              <th style="width: 28%;">row identity</th>
              <th style="width: 18%;">컬럼명</th>
              <th style="width: 27%;">source 값</th>
              <th style="width: 27%;">target 값</th>
            </tr>
          </thead>
          <tbody>
{difference_rows}
          </tbody>
        </table>
      </div>
    """


def _matched_sample_row(table) -> str:
    return f"""
          <tr>
            <td colspan="4">
              <details>
                <summary class="matched-summary">
                  <span>{escape(table.table.schema)}</span>
                  <span class="summary-table">{escape(table.table.name)}</span>
                  <span><span class="badge {escape(table.status)}">{escape(_status_label(table.status))}</span></span>
                  <span>정상 샘플 {len(table.checksum.matched_samples)}건. 클릭하면 이관 전 -> 이관 후 값을 비교합니다.</span>
                </summary>
                {_matched_sample_panel(table)}
              </details>
            </td>
          </tr>
    """


def _matched_sample_panel(table) -> str:
    rows = "\n".join(
        f"""
          <tr>
            <td>{escape(sample.row_identity)}</td>
            <td><pre class="row-json">{escape(_json_values(sample.source_values))}</pre></td>
            <td><pre class="row-json">{escape(_json_values(sample.target_values))}</pre></td>
          </tr>
        """
        for sample in table.checksum.matched_samples
    )
    return f"""
      <div class="matched-panel">
        <table>
          <thead>
            <tr>
              <th style="width: 22%;">row identity</th>
              <th style="width: 39%;">이관 전 source</th>
              <th style="width: 39%;">이관 후 target</th>
            </tr>
          </thead>
          <tbody>
{rows}
          </tbody>
        </table>
      </div>
    """


def _json_values(values: dict[str, str]) -> str:
    return json.dumps(values, ensure_ascii=False, indent=2)
