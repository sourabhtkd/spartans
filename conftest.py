def pytest_addoption(parser):
    parser.addoption(
        "--record",
        action="store_true",
        default=False,
        help="golden gate: run LLM-backed cases against the live provider and save cassettes",
    )
