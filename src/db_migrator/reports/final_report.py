from __future__ import annotations

import csv
import json
from dataclasses import asdict
from html import escape
from pathlib import Path

from db_migrator.core.validation import ValidationReport, ValidationStatus
from db_migrator.reports.labels import display_timestamp, option_label, result_label


def write_validation_report(report: ValidationReport, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(report, output_dir / "summary.json")
    _write_csv(report, output_dir / "tables.csv")
    _write_html(report, output_dir / "summary.html")
    _write_errors(report, output_dir / "errors.csv")
    _write_differences(report, output_dir / "differences.csv")
    _write_schema_objects(report, output_dir / "schema-objects.csv")
    _write_execution_artifacts(report, output_dir / "execution-artifacts.csv")


def _write_json(report: ValidationReport, output_path: Path) -> None:
    payload = asdict(report)
    payload["summary"] = _summary_metrics(report)
    payload["table_summaries"] = [_table_metrics(table) for table in report.tables]
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(report: ValidationReport, output_path: Path) -> None:
    with output_path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(
            [
                "스키마",
                "테이블",
                "상태",
                "원본_행수",
                "대상_행수",
                "행수검증_상태",
                "체크섬_상태",
                "매칭_행수",
                "행수_차이",
                "오류_수",
                "값차이_수",
                "정상샘플_수",
                "원본_체크섬",
                "대상_체크섬",
            ]
        )
        data_sync_by_table = _data_sync_by_table(report)
        for table in _visible_table_results(report):
            metrics = _table_metrics(table)
            data_sync_artifact = data_sync_by_table.get(table.table.name)
            source_rows = data_sync_artifact.changed_rows if data_sync_artifact is not None else table.row_count.source_rows
            target_rows = data_sync_artifact.changed_rows if data_sync_artifact is not None else table.row_count.target_rows
            writer.writerow(
                [
                    metrics["schema"],
                    metrics["table"],
                    table.status,
                    source_rows,
                    target_rows,
                    table.row_count.status,
                    table.checksum.status,
                    metrics["matched_rows"],
                    metrics["row_count_delta"],
                    metrics["error_count"],
                    metrics["difference_count"],
                    metrics["matched_sample_count"],
                    table.checksum.source_checksum,
                    table.checksum.target_checksum,
                ]
            )


def _write_html(report: ValidationReport, output_path: Path) -> None:
    summary = _summary_metrics(report)
    issue_rows = "\n".join(_issue_rows(table) for table in report.tables if table.status != ValidationStatus.MATCHED)
    schema_object_rows = "\n".join(
        _schema_object_row(schema_object)
        for schema_object in report.schema_objects
        if _is_schema_object_action_required(schema_object.status)
    )
    if not schema_object_rows:
        schema_object_rows = """
          <tr>
            <td colspan="6" class="empty-state">스키마 객체 관련 이슈 및 조치사항이 없습니다.</td>
          </tr>
        """
    if not issue_rows:
        issue_rows = """
          <tr>
            <td colspan="6" class="empty-state">테이블 관련 이슈 및 조치사항이 없습니다.</td>
          </tr>
        """
    schema_object_lookup = _schema_object_lookup(report)
    execution_artifact_rows = "\n".join(
        _execution_artifact_row(artifact, schema_object_lookup) for artifact in report.execution_artifacts
    )
    if not execution_artifact_rows:
        execution_artifact_rows = """
          <tr>
            <td colspan="7" class="empty-state">확인된 DDL/인덱스/FK 작업 내역이 없습니다.</td>
          </tr>
        """
    verification_audit_rows = "\n".join(_verification_audit_rows(report))
    data_sync_by_table = _data_sync_by_table(report)
    visible_tables = _visible_table_results(report)
    table_summary_rows = "\n".join(_table_summary_row(table, data_sync_by_table.get(table.table.name)) for table in visible_tables)
    if not table_summary_rows:
        table_summary_rows = """
          <tr>
            <td colspan="8" class="empty-state">이번 실행에서 검증할 이관/수정/삭제 테이블이 없습니다.</td>
          </tr>
        """
    html = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>Jigration 검증 리포트</title>
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
    .execution-summary {{
      display: grid;
      grid-template-columns: 10% 20% 10% 10% 18% 16% 16%;
      align-items: start;
      min-height: 48px;
      cursor: pointer;
      list-style: none;
    }}
    .execution-summary::-webkit-details-marker {{
      display: none;
    }}
    .execution-summary > span {{
      padding: var(--space-150) var(--space-200);
      color: var(--color-text-normal);
      font-size: 14px;
      line-height: 20px;
      word-break: break-word;
    }}
    .execution-summary > .summary-table {{
      color: var(--color-primary);
      font-weight: 700;
    }}
    .execution-summary:hover > span {{
      background: var(--color-canvas-200);
    }}
    .execution-summary:focus-visible {{
      border-radius: var(--radius-200);
      outline: 2px solid var(--color-primary);
      outline-offset: 2px;
    }}
    details.table-summary-details {{
      width: 100%;
    }}
    .table-summary {{
      display: grid;
      grid-template-columns: 10% 16% 10% 12% 12% 12% 14% 14%;
      align-items: start;
      min-height: 48px;
      cursor: pointer;
      list-style: none;
    }}
    .table-summary::-webkit-details-marker {{
      display: none;
    }}
    .table-summary > span {{
      padding: var(--space-150) var(--space-200);
      color: var(--color-text-normal);
      font-size: 14px;
      line-height: 20px;
      word-break: break-word;
    }}
    .table-summary > .summary-table {{
      color: var(--color-primary);
      font-weight: 700;
    }}
    .table-summary:hover > span {{
      background: var(--color-canvas-200);
    }}
    .table-summary:focus-visible {{
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
        <h1>Jigration 검증 리포트</h1>
        <p>실제로 이관한 내용과 이관 후 비교 검증한 결과를 확인하는 리포트입니다. 이슈 상세는 하단의 이슈 및 권장 조치와 스키마 객체 검증에서 확인하세요.</p>
      </div>
      <div class="summary-grid">
        <div class="metric"><span>검증 결과</span><strong>{escape(result_label(report.status))}</strong></div>
        <div class="metric"><span>검증 테이블 수</span><strong>{summary["table_count"]}</strong></div>
        <div class="metric"><span>전체 데이터 수</span><strong>{_summary_data_metric(report, summary["total_target_rows"])}</strong></div>
        <div class="metric"><span>검증 데이터 수</span><strong>{_summary_data_metric(report, summary["total_target_rows"])}</strong></div>
        <div class="metric"><span>이슈 수</span><strong>{summary["issue_count"]}</strong></div>
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
      <h2>검증 수행 내역</h2>
      <p>이번 리포트가 어떤 작업 내역을 근거로 삼았고, 어떤 항목을 source 후보와 target 실제 상태로 비교했는지 요약합니다.</p>
      <table>
        <thead>
          <tr>
            <th style="width: 18%;">구분</th>
            <th style="width: 16%;">검증 기준</th>
            <th style="width: 16%;">대상 수</th>
            <th style="width: 16%;">이슈 수</th>
            <th style="width: 34%;">근거</th>
          </tr>
        </thead>
        <tbody>
{verification_audit_rows}
        </tbody>
      </table>
    </section>

    <section>
      <h2>테이블별 검증 요약</h2>
      <p>각 테이블의 전체 데이터 수, 이관 수, 성공 여부와 다음 조치를 한 줄로 확인합니다. 행을 펼치면 검산 샘플을 비교할 수 있습니다.</p>
      <table>
        <thead>
          <tr>
            <th style="width: 10%;">스키마</th>
            <th style="width: 16%;">테이블명</th>
            <th style="width: 10%;">검증 결과</th>
            <th style="width: 12%;">전체 데이터 수</th>
            <th style="width: 12%;">이관 수</th>
            <th style="width: 12%;">행 수 차이</th>
            <th style="width: 14%;">대표 이슈</th>
            <th style="width: 14%;">다음 조치</th>
          </tr>
        </thead>
        <tbody>
{table_summary_rows}
        </tbody>
      </table>
    </section>

    <section>
      <h2>이슈 및 권장 조치</h2>
      <p>문제가 있는 테이블과 스키마 객체만 표시합니다. 조치할 항목이 없으면 이슈 없음으로 표시합니다.</p>
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
      <h2>스키마 객체 검증</h2>
      <p>source 이관 후보와 target 실제 스키마를 같은 모델로 비교합니다. 테이블, 컬럼, PK, 자동증가 속성, 인덱스, FK, 비테이블 객체의 후보/실제/조치 여부를 확인합니다.</p>
      <table>
        <thead>
          <tr>
            <th style="width: 14%;">객체 유형</th>
            <th style="width: 24%;">객체명</th>
            <th style="width: 12%;">상태</th>
            <th style="width: 18%;">source 후보</th>
            <th style="width: 18%;">target 실제</th>
            <th style="width: 14%;">권장 조치</th>
          </tr>
        </thead>
        <tbody>
{schema_object_rows}
        </tbody>
      </table>
    </section>

    <section>
      <h2>실제 작업 내역</h2>
      <p>apply-ddl, apply-indexes, apply-foreign-keys 단계에서 실제 수행한 DDL/FK/INDEX 작업을 source 후보와 target 실제 비교 결과에 연결합니다. 행을 펼치면 실행 SQL을 확인할 수 있습니다.</p>
      <table>
        <thead>
          <tr>
            <th style="width: 10%;">유형</th>
            <th style="width: 20%;">객체명</th>
            <th style="width: 10%;">작업</th>
            <th style="width: 10%;">결과</th>
            <th style="width: 18%;">메시지</th>
            <th style="width: 16%;">비교 결과</th>
            <th style="width: 16%;">실행 SQL</th>
          </tr>
        </thead>
        <tbody>
{execution_artifact_rows}
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
    rows = _context_rows(
        [
            ("실행 시각", display_timestamp(report.metadata.generated_at, report.metadata.checksum_timezone)),
            ("실행 방식", option_label(report.metadata.migration_mode)),
            ("기존 테이블 처리", option_label(report.metadata.existing_table_policy)),
            ("시간대 기준", report.metadata.checksum_timezone or "-"),
            ("일시 비교 정밀도", option_label(report.metadata.checksum_datetime_precision)),
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


def _summary_metrics(report: ValidationReport) -> dict[str, int | None]:
    if report.data_sync_artifacts:
        return _data_sync_summary_metrics(report)
    table_metrics = [_table_metrics(table) for table in report.tables]
    total_source_rows = _sum_optional_int(metric["source_rows"] for metric in table_metrics)
    total_target_rows = _sum_optional_int(metric["target_rows"] for metric in table_metrics)
    successful_table_count = sum(1 for table in report.tables if table.status == ValidationStatus.MATCHED)
    successful_migrated_rows = _sum_successful_target_rows(report)
    table_issue_count = _issue_count(report)
    schema_object_issue_count = sum(1 for schema_object in report.schema_objects if _is_schema_object_action_required(schema_object.status))
    failed_execution_artifact_count = sum(1 for artifact in report.execution_artifacts if not artifact.success)
    return {
        "table_count": len(report.tables),
        "successful_table_count": successful_table_count,
        "matched_table_count": successful_table_count,
        "mismatched_table_count": sum(1 for table in report.tables if table.status == ValidationStatus.MISMATCHED),
        "failed_table_count": sum(1 for table in report.tables if table.status == ValidationStatus.FAILED),
        "skipped_table_count": sum(1 for table in report.tables if table.status == ValidationStatus.SKIPPED),
        "total_source_rows": total_source_rows,
        "total_target_rows": total_target_rows,
        "total_matched_rows": _sum_optional_int(metric["matched_rows"] for metric in table_metrics),
        "successful_migrated_rows": successful_migrated_rows,
        "total_row_count_delta": None if total_source_rows is None or total_target_rows is None else total_source_rows - total_target_rows,
        "total_error_count": sum(int(metric["error_count"]) for metric in table_metrics),
        "total_difference_count": sum(int(metric["difference_count"]) for metric in table_metrics),
        "total_matched_sample_count": sum(int(metric["matched_sample_count"]) for metric in table_metrics),
        "schema_object_count": len(report.schema_objects),
        "table_issue_count": table_issue_count,
        "schema_object_issue_count": schema_object_issue_count,
        "issue_count": table_issue_count + schema_object_issue_count + failed_execution_artifact_count,
        "execution_artifact_count": len(report.execution_artifacts),
        "failed_execution_artifact_count": failed_execution_artifact_count,
    }


def _data_sync_summary_metrics(report: ValidationReport) -> dict[str, int | None]:
    changed_data_artifacts = tuple(artifact for artifact in report.data_sync_artifacts if artifact.changed_rows > 0)
    ddl_changed_tables = _ddl_changed_table_names(report)
    changed_table_names = {artifact.table for artifact in changed_data_artifacts} | ddl_changed_tables
    changed_rows = sum(artifact.changed_rows for artifact in changed_data_artifacts)
    failed_execution_artifact_count = sum(1 for artifact in report.execution_artifacts if not artifact.success)
    schema_object_issue_count = sum(1 for schema_object in report.schema_objects if _is_schema_object_action_required(schema_object.status))
    table_issue_count = sum(1 for table in _visible_table_results(report) if table.status != ValidationStatus.MATCHED)
    return {
        "table_count": len(changed_table_names),
        "successful_table_count": sum(1 for table in _visible_table_results(report) if table.status == ValidationStatus.MATCHED),
        "matched_table_count": sum(1 for table in _visible_table_results(report) if table.status == ValidationStatus.MATCHED),
        "mismatched_table_count": sum(1 for table in _visible_table_results(report) if table.status == ValidationStatus.MISMATCHED),
        "failed_table_count": sum(1 for table in _visible_table_results(report) if table.status == ValidationStatus.FAILED),
        "skipped_table_count": sum(1 for table in _visible_table_results(report) if table.status == ValidationStatus.SKIPPED),
        "total_source_rows": changed_rows,
        "total_target_rows": changed_rows,
        "total_matched_rows": changed_rows - table_issue_count if table_issue_count == 0 else 0,
        "successful_migrated_rows": changed_rows - table_issue_count if table_issue_count == 0 else 0,
        "total_row_count_delta": 0,
        "total_error_count": table_issue_count,
        "total_difference_count": 0,
        "total_matched_sample_count": 0,
        "schema_object_count": len(report.schema_objects),
        "table_issue_count": table_issue_count,
        "schema_object_issue_count": schema_object_issue_count,
        "issue_count": table_issue_count + schema_object_issue_count + failed_execution_artifact_count,
        "execution_artifact_count": len(report.execution_artifacts),
        "failed_execution_artifact_count": failed_execution_artifact_count,
    }


def _summary_data_metric(report: ValidationReport, value: int | None) -> str:
    if _is_schema_only_report(report):
        return "-"
    return _format_optional_int(value)


def _is_schema_only_report(report: ValidationReport) -> bool:
    return report.metadata.migration_mode in {"ddl_only", "manual_ddl"}


def _table_metrics(table) -> dict[str, int | str | None]:
    source_rows = table.row_count.source_rows
    target_rows = table.row_count.target_rows
    matched_rows = _matched_row_count(table)
    row_count_delta = None if source_rows is None or target_rows is None else source_rows - target_rows
    return {
        "schema": table.table.schema,
        "table": _table_label(table),
        "source_table": _qualified_table_label(table.table),
        "target_table": _qualified_table_label(table.target_table) if table.target_table is not None else None,
        "status": table.status,
        "source_rows": source_rows,
        "target_rows": target_rows,
        "matched_rows": matched_rows,
        "row_count_delta": row_count_delta,
        "error_count": _table_error_count(table),
        "difference_count": len(table.checksum.differences),
        "matched_sample_count": len(table.checksum.matched_samples),
        "row_count_status": table.row_count.status,
        "checksum_status": table.checksum.status,
    }


def _data_sync_by_table(report: ValidationReport) -> dict[str, object]:
    return {artifact.table: artifact for artifact in report.data_sync_artifacts}


def _visible_table_results(report: ValidationReport) -> tuple[object, ...]:
    if not report.data_sync_artifacts:
        return report.tables
    changed_table_names = {artifact.table for artifact in report.data_sync_artifacts if artifact.changed_rows > 0}
    changed_table_names |= _ddl_changed_table_names(report)
    return tuple(table for table in report.tables if table.table.name in changed_table_names)


def _ddl_changed_table_names(report: ValidationReport) -> set[str]:
    changed_names: set[str] = set()
    for artifact in report.execution_artifacts:
        if not artifact.success:
            continue
        if artifact.artifact_type != "DDL":
            continue
        if artifact.action in {"sync_existing", "skip"} and not artifact.ddl:
            continue
        changed_names.add(artifact.object_name.rsplit(".", 1)[-1])
    return changed_names


def _table_label(table) -> str:
    source_label = _qualified_table_label(table.table)
    target_table = table.target_table
    if target_table is None:
        return table.table.name
    target_label = _qualified_table_label(target_table)
    if source_label == target_label:
        return table.table.name
    return f"{source_label} -> {target_label}"


def _qualified_table_label(table_ref) -> str:
    return f"{table_ref.schema}.{table_ref.name}"


def _matched_row_count(table) -> int | None:
    source_rows = table.row_count.source_rows
    target_rows = table.row_count.target_rows
    if source_rows is None or target_rows is None:
        return None
    if table.row_count.status == ValidationStatus.MATCHED:
        return source_rows
    return min(source_rows, target_rows)


def _table_error_count(table) -> int:
    return int(table.row_count.status in {ValidationStatus.MISMATCHED, ValidationStatus.FAILED}) + int(
        table.checksum.status in {ValidationStatus.MISMATCHED, ValidationStatus.FAILED}
    )


def _sum_optional_int(values) -> int | None:
    resolved_values = list(values)
    if any(value is None for value in resolved_values):
        return None
    return sum(int(value) for value in resolved_values)


def _sum_successful_target_rows(report: ValidationReport) -> int | None:
    successful_target_rows = [
        table.row_count.target_rows
        for table in report.tables
        if table.status == ValidationStatus.MATCHED
    ]
    return _sum_optional_int(successful_target_rows)


def _write_errors(report: ValidationReport, output_path: Path) -> None:
    with output_path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(["스키마", "테이블", "검증항목", "상태", "메시지"])
        for table in report.tables:
            metrics = _table_metrics(table)
            if table.row_count.status in {ValidationStatus.MISMATCHED, ValidationStatus.FAILED}:
                writer.writerow([metrics["schema"], metrics["table"], "row_count", table.row_count.status, table.row_count.message])
            if table.checksum.status in {ValidationStatus.MISMATCHED, ValidationStatus.FAILED}:
                writer.writerow([metrics["schema"], metrics["table"], "체크섬", table.checksum.status, table.checksum.message])


def _write_differences(report: ValidationReport, output_path: Path) -> None:
    with output_path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(["스키마", "테이블", "행_식별값", "컬럼", "원본_값", "대상_값"])
        for table in report.tables:
            metrics = _table_metrics(table)
            for difference in table.checksum.differences:
                writer.writerow(
                    [
                        metrics["schema"],
                        metrics["table"],
                        difference.row_identity,
                        difference.column,
                        difference.source_value,
                        difference.target_value,
                    ]
                )


def _write_schema_objects(report: ValidationReport, output_path: Path) -> None:
    with output_path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(["객체유형", "객체명", "상태", "source_후보", "target_실제", "권장조치"])
        for schema_object in report.schema_objects:
            writer.writerow(
                [
                    schema_object.object_type,
                    schema_object.object_name,
                    schema_object.status,
                    schema_object.source_detail,
                    schema_object.target_detail,
                    schema_object.action,
                ]
            )


def _write_execution_artifacts(report: ValidationReport, output_path: Path) -> None:
    with output_path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(["유형", "객체명", "작업", "성공여부", "메시지", "DDL", "원본파일"])
        for artifact in report.execution_artifacts:
            writer.writerow(
                [
                    artifact.artifact_type,
                    artifact.object_name,
                    artifact.action,
                    artifact.success,
                    artifact.message,
                    artifact.ddl,
                    artifact.source_file,
                ]
            )


def _verification_audit_rows(report: ValidationReport) -> tuple[str, ...]:
    table_count = len(report.tables)
    return (
        _verification_audit_row(
            category="행 수 검증",
            basis="source count vs target count",
            target_count=table_count,
            issue_count=sum(1 for table in report.tables if table.row_count.status in {ValidationStatus.MISMATCHED, ValidationStatus.FAILED}),
            evidence="원본/대상 DB에서 테이블별 행 수를 조회해 비교했습니다.",
        ),
        _verification_audit_row(
            category="검산 샘플 검증",
            basis="source sample checksum vs target sample checksum",
            target_count=table_count,
            issue_count=sum(1 for table in report.tables if table.checksum.status in {ValidationStatus.MISMATCHED, ValidationStatus.FAILED}),
            evidence="PK 또는 정렬 가능한 기준으로 상/하위 샘플을 정규화해 checksum을 비교했습니다.",
        ),
        _verification_audit_row(
            category="DDL/스키마 검증",
            basis="source 후보 객체 vs target 실제 객체",
            target_count=len(report.schema_objects),
            issue_count=sum(1 for schema_object in report.schema_objects if _is_schema_object_action_required(schema_object.status)),
            evidence="테이블, 컬럼, PK, 자동증가, 인덱스, FK, 비테이블 객체를 동일 비교 모델로 확인했습니다.",
        ),
        _verification_audit_row(
            category="실제 작업 내역",
            basis="실행 리포트 artifact",
            target_count=len(report.execution_artifacts),
            issue_count=sum(1 for artifact in report.execution_artifacts if not artifact.success),
            evidence="apply-ddl, apply-indexes, apply-foreign-keys 결과 파일에서 실제 수행 작업을 읽었습니다.",
        ),
    )


def _verification_audit_row(
    *,
    category: str,
    basis: str,
    target_count: int,
    issue_count: int,
    evidence: str,
) -> str:
    return f"""
          <tr>
            <td class="message">{escape(category)}</td>
            <td>{escape(basis)}</td>
            <td>{target_count}</td>
            <td>{issue_count}</td>
            <td>{escape(evidence)}</td>
          </tr>
    """


def _issue_count(report: ValidationReport) -> int:
    return sum(1 for table in report.tables if table.status != ValidationStatus.MATCHED)


def _table_summary_row(table, data_sync_artifact=None) -> str:
    metrics = _table_metrics(table)
    source_rows = data_sync_artifact.changed_rows if data_sync_artifact is not None else metrics["source_rows"]
    target_rows = data_sync_artifact.changed_rows if data_sync_artifact is not None else metrics["target_rows"]
    row_delta = "0" if data_sync_artifact is not None else _row_count_delta_label(table)
    representative_issue = _data_sync_summary_label(data_sync_artifact) if data_sync_artifact is not None else _representative_issue(table)
    next_action = "변경분 source/target 검증 완료" if data_sync_artifact is not None and table.status == ValidationStatus.MATCHED else _next_action(table)
    return f"""
          <tr>
            <td colspan="8">
              <details class="table-summary-details">
                <summary class="table-summary">
                  <span>{escape(str(metrics["schema"]))}</span>
                  <span class="summary-table">{escape(str(metrics["table"]))}</span>
                  <span><span class="badge {escape(str(metrics["status"]))}">{escape(result_label(str(metrics["status"])))}</span></span>
                  <span>{_format_optional_int(source_rows)}</span>
                  <span>{_format_optional_int(target_rows)}</span>
                  <span>{escape(row_delta)}</span>
                  <span>{escape(representative_issue)}</span>
                  <span class="action">{escape(next_action)}</span>
                </summary>
                {_table_detail_panel(table, data_sync_artifact)}
              </details>
            </td>
          </tr>
    """


def _data_sync_summary_label(data_sync_artifact) -> str:
    if data_sync_artifact is None:
        return "이슈 없음"
    return (
        f"추가 {data_sync_artifact.rows_inserted}, "
        f"수정 {data_sync_artifact.rows_updated}, "
        f"삭제 {data_sync_artifact.rows_deleted}"
    )


def _table_detail_panel(table, data_sync_artifact=None) -> str:
    if data_sync_artifact is not None:
        return _data_sync_validation_panel(data_sync_artifact)
    return _matched_sample_panel(table)


def _data_sync_validation_panel(data_sync_artifact) -> str:
    return f"""
      <div class="matched-panel">
        <table>
          <thead>
            <tr>
              <th style="width: 20%;">검증 항목</th>
              <th style="width: 16%;">검증 데이터 수</th>
              <th style="width: 16%;">추가</th>
              <th style="width: 16%;">수정</th>
              <th style="width: 16%;">삭제</th>
              <th style="width: 16%;">검증 방식</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>이관 데이터 검증</td>
              <td>{data_sync_artifact.changed_rows}</td>
              <td>{data_sync_artifact.rows_inserted}</td>
              <td>{data_sync_artifact.rows_updated}</td>
              <td>{data_sync_artifact.rows_deleted}</td>
              <td>{escape(_data_sync_validation_basis(data_sync_artifact))}</td>
            </tr>
          </tbody>
        </table>
      </div>
    """


def _data_sync_validation_basis(data_sync_artifact) -> str:
    checks: list[str] = []
    if data_sync_artifact.rows_inserted or data_sync_artifact.rows_updated:
        checks.append("insert/update 대상 row를 target 반영 후 비교")
    if data_sync_artifact.rows_deleted:
        checks.append("delete 대상 key가 target에서 제거됐는지 확인")
    return ", ".join(checks) if checks else "변경 대상 없음"


def _row_count_delta_label(table) -> str:
    source_rows = table.row_count.source_rows
    target_rows = table.row_count.target_rows
    if source_rows is None or target_rows is None:
        return "확인 불가"
    delta = source_rows - target_rows
    if delta == 0:
        return "0"
    if delta > 0:
        return f"대상 {delta}행 부족"
    return f"대상 {abs(delta)}행 초과"


def _representative_issue(table) -> str:
    if table.row_count.status == ValidationStatus.FAILED and table.row_count.message:
        return table.row_count.message
    if table.row_count.status == ValidationStatus.MISMATCHED:
        return _row_count_delta_label(table)
    if table.checksum.status == ValidationStatus.FAILED and table.checksum.message:
        return table.checksum.message
    if table.checksum.differences:
        difference = table.checksum.differences[0]
        return f"{difference.row_identity} / {difference.column}"
    if table.status == ValidationStatus.MATCHED:
        return "이슈 없음"
    return "상세 확인 필요"


def _next_action(table) -> str:
    if table.row_count.status in {ValidationStatus.FAILED, ValidationStatus.MISMATCHED}:
        return _row_count_action(table)
    if table.checksum.status in {ValidationStatus.FAILED, ValidationStatus.MISMATCHED}:
        return _checksum_action(table)
    if table.row_count.status == ValidationStatus.SKIPPED or table.checksum.status == ValidationStatus.SKIPPED:
        return "건너뛴 검증 항목이 필요한지 확인하세요."
    return "추가 조치가 필요하지 않습니다."


def _schema_object_row(schema_object) -> str:
    return f"""
          <tr>
            <td>{escape(schema_object.object_type)}</td>
            <td class="message">{escape(schema_object.object_name)}</td>
            <td><span class="badge {_schema_object_badge_class(schema_object.status)}">{escape(_schema_object_status_label(schema_object.status))}</span></td>
            <td>{escape(schema_object.source_detail)}</td>
            <td>{escape(schema_object.target_detail)}</td>
            <td class="action">{escape(schema_object.action)}</td>
          </tr>
    """


def _schema_object_lookup(report: ValidationReport) -> dict[str, object]:
    lookup: dict[str, object] = {}
    for schema_object in report.schema_objects:
        lookup.setdefault(schema_object.object_name, schema_object)
        tail_key = _object_tail_key(schema_object.object_name)
        if tail_key:
            lookup.setdefault(tail_key, schema_object)
    return lookup


def _object_tail_key(object_name: str) -> str | None:
    parts = object_name.split(".")
    if len(parts) < 2:
        return None
    return ".".join(parts[1:])


def _find_schema_object_for_artifact(artifact, schema_object_lookup: dict[str, object]):
    return schema_object_lookup.get(artifact.object_name) or schema_object_lookup.get(_object_tail_key(artifact.object_name) or "")


def _execution_artifact_row(artifact, schema_object_lookup: dict[str, object]) -> str:
    status = "matched" if artifact.success else "failed"
    schema_object = _find_schema_object_for_artifact(artifact, schema_object_lookup)
    comparison_label = _execution_comparison_label(schema_object)
    source_detail = schema_object.source_detail if schema_object is not None else "비교 객체 없음"
    target_detail = schema_object.target_detail if schema_object is not None else "비교 객체 없음"
    action = schema_object.action if schema_object is not None else "실제 실행 SQL만 확인됨"
    return f"""
          <tr>
            <td colspan="7">
              <details class="issue-details">
                <summary class="execution-summary">
                  <span>{escape(artifact.artifact_type)}</span>
                  <span class="summary-table">{escape(artifact.object_name)}</span>
                  <span>{escape(artifact.action)}</span>
                  <span><span class="badge {status}">{escape("성공" if artifact.success else "실패")}</span></span>
                  <span>{escape(artifact.message)}</span>
                  <span>{escape(comparison_label)}</span>
                  <span><code>{escape("펼쳐보기")}</code></span>
                </summary>
                <div class="difference-panel">
                  <table>
                    <thead>
                      <tr>
                        <th style="width: 22%;">source 후보</th>
                        <th style="width: 22%;">target 실제</th>
                        <th style="width: 22%;">비교/권장 조치</th>
                        <th style="width: 34%;">실행 SQL</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr>
                        <td>{escape(source_detail)}</td>
                        <td>{escape(target_detail)}</td>
                        <td>{escape(action)}</td>
                        <td><code>{escape(artifact.ddl or "-")}</code></td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </details>
            </td>
          </tr>
    """


def _execution_comparison_label(schema_object) -> str:
    if schema_object is None:
        return "비교 결과 없음"
    return _schema_object_status_label(schema_object.status)


def _schema_object_badge_class(status: str) -> str:
    if status == "matched":
        return "matched"
    if status == "manual_review":
        return "skipped"
    if status == "target_only":
        return "skipped"
    return "mismatched"


def _schema_object_status_label(status: str) -> str:
    return {
        "matched": "일치",
        "missing": "누락",
        "mismatched": "불일치",
        "manual_review": "수동 검토",
        "target_only": "대상만 있음",
    }.get(status, status)


def _is_schema_object_issue(status: str) -> bool:
    return status in {"missing", "mismatched", "target_only"}


def _is_schema_object_action_required(status: str) -> bool:
    return status != "matched"


def _issue_rows(table) -> str:
    rows = []
    metrics = _table_metrics(table)
    if table.row_count.status in {ValidationStatus.MISMATCHED, ValidationStatus.FAILED}:
        rows.append(
            _issue_row(
                schema=str(metrics["schema"]),
                table=str(metrics["table"]),
                validation="row_count",
                status=table.row_count.status,
                detail=_row_count_detail(table),
                action=_row_count_action(table),
            )
        )
    if table.checksum.status in {ValidationStatus.MISMATCHED, ValidationStatus.FAILED}:
        rows.append(
            _issue_row(
                schema=str(metrics["schema"]),
                table=str(metrics["table"]),
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
        f"원본 행 수={_format_optional_int(table.row_count.source_rows)} "
        f"대상 행 수={_format_optional_int(table.row_count.target_rows)}"
    )


def _checksum_detail(table) -> str:
    if table.checksum.message:
        return escape(table.checksum.message)
    if table.checksum.differences:
        difference_count = len(table.checksum.differences)
        return f"검산 샘플에서 값 차이 {difference_count}건이 발견되었습니다."
    return (
        f"원본 샘플 해시=<code>{escape(_short_checksum(table.checksum.source_checksum))}</code> "
        f"대상 샘플 해시=<code>{escape(_short_checksum(table.checksum.target_checksum))}</code>"
    )


def _row_count_action(table) -> str:
    if table.row_count.status == ValidationStatus.FAILED:
        return "원본/대상 행 수 조회 권한을 확인한 뒤 validate를 다시 실행하세요."
    source_rows = table.row_count.source_rows
    target_rows = table.row_count.target_rows
    if source_rows is None or target_rows is None:
        return "원본/대상 행 수 조회 결과를 확인한 뒤 validate를 다시 실행하세요."
    if source_rows > target_rows:
        missing_rows = source_rows - target_rows
        return f"대상에 {missing_rows}행이 부족합니다. 해당 테이블을 migrate-data 또는 resume으로 다시 이관한 뒤 validate를 재실행하세요."
    if target_rows > source_rows:
        extra_rows = target_rows - source_rows
        return f"대상에 {extra_rows}행이 더 많습니다. 중복 데이터나 기존 대상 잔여 데이터를 확인한 뒤 재시도하세요."
    return "행 수는 일치합니다. 샘플 값 상세에서 값 단위 차이를 확인하세요."


def _checksum_action(table) -> str:
    if table.checksum.status == ValidationStatus.FAILED:
        return "샘플 조회 권한, 지원하지 않는 컬럼 값, 정규화 설정을 확인하세요."
    if not table.checksum.differences:
        return "샘플 해시는 다르지만 검산 샘플에서 컬럼 차이를 찾지 못했습니다. 검산 범위를 넓혀 검증을 재실행하세요."

    columns = _difference_columns(table)
    if _has_row_presence_difference(table):
        return f"{columns} 기준 샘플 행 구성이 다릅니다. 기본키와 행 수를 확인한 뒤 해당 테이블을 다시 검증하세요."
    if _has_missing_column_difference(table):
        return f"{columns} 컬럼이 한쪽에 없습니다. 조회 컬럼 목록, 생성 컬럼, 대상 DDL을 확인하세요."
    if _has_temporal_difference(table):
        return f"{columns}의 timezone 또는 초 이하 정밀도를 확인하세요. 타입 매핑/정규화 설정을 맞춘 뒤 영향 행을 재이관하세요."
    if _has_numeric_difference(table):
        return f"{columns}의 숫자 정밀도, 소수 자릿수, 반올림 여부를 확인하세요. 대상 컬럼 타입을 맞춘 뒤 영향 행을 재이관하세요."
    if _has_json_difference(table):
        return f"{columns}의 JSON 직렬화 방식을 확인하세요. json/jsonb 의미 차이와 정규화된 페이로드를 비교하세요."
    return f"{columns}의 값 변환을 확인하세요. 정규화 후에도 원본/대상 샘플 값이 다릅니다."


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
        "checksum": "검산 샘플",
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
              <th style="width: 28%;">행 식별값</th>
              <th style="width: 18%;">컬럼명</th>
              <th style="width: 27%;">원본 값</th>
              <th style="width: 27%;">대상 값</th>
            </tr>
          </thead>
          <tbody>
{difference_rows}
          </tbody>
        </table>
      </div>
    """


def _matched_sample_panel(table) -> str:
    if not table.checksum.matched_samples:
        return """
      <div class="matched-panel">
        <table>
          <tbody>
            <tr>
              <td colspan="3" class="empty-state">표시할 검산 샘플이 없습니다.</td>
            </tr>
          </tbody>
        </table>
      </div>
    """
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
              <th style="width: 22%;">행 식별값</th>
              <th style="width: 39%;">원본 값</th>
              <th style="width: 39%;">대상 값</th>
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
