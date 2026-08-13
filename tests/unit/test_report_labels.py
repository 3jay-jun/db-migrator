from db_migrator.reports.labels import option_label


def test_existing_table_policy_labels_describe_target_table_behavior() -> None:
    assert option_label("skip") == "덮어쓰기"
    assert option_label("append") == "추가"
