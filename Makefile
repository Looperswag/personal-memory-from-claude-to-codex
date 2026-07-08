.PHONY: test self-test

test:
	python3 -m unittest discover -s tests -v

self-test:
	python3 plugins/personal-memory/scripts/personal_memory_mcp.py --self-test
