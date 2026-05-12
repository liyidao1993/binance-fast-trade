# ------------------------------------------------------------------
# 币安合约快速交易系统 · 开发辅助 Makefile
# Windows 下建议用 git-bash 或 WSL；纯 PowerShell 可直接复制命令执行
# ------------------------------------------------------------------

PY ?= python
PIP ?= pip

.PHONY: help install install-dev test test-v lint fmt run clean tree

help:
	@echo "Targets:"
	@echo "  make install        安装运行时依赖"
	@echo "  make install-dev    安装开发依赖（含 pytest / ruff）"
	@echo "  make test           运行所有单测"
	@echo "  make test-v         运行所有单测（verbose）"
	@echo "  make lint           ruff 静态检查"
	@echo "  make fmt            ruff format 格式化"
	@echo "  make run            uvicorn 开发模式启动"
	@echo "  make clean          清理缓存"

install:
	cd backend && $(PIP) install -e .

install-dev:
	cd backend && $(PIP) install -e ".[dev]"

test:
	cd backend && pytest

test-v:
	cd backend && pytest -vv -s

lint:
	cd backend && ruff check .

fmt:
	cd backend && ruff format .

run:
	cd backend && uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

clean:
	powershell -Command "Get-ChildItem -Path . -Include __pycache__,.pytest_cache,.mypy_cache,.ruff_cache -Recurse -Force | Remove-Item -Recurse -Force"
