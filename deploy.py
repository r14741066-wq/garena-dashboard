"""
deploy.py — 將 dashboard.html 推送到 GitHub Pages

首次設定：python3 deploy.py --setup --username YOUR_USERNAME
日常更新：python3 deploy.py  （由 dashboard_generator.py 自動呼叫）
"""
import argparse
import subprocess
import sys
from pathlib import Path

REPO_NAME  = "garena-dashboard"
DOCS_DIR   = Path(__file__).parent / "docs"
HTML_SRC   = Path(__file__).parent / "dashboard.html"
INDEX_DEST = DOCS_DIR / "index.html"


def setup(username: str) -> bool:
    """首次設定：初始化 git、建立 GitHub repo、推送。"""
    project = Path(__file__).parent

    print(f"\n=== GitHub Pages 初始化 ===")
    print(f"帳號：{username}　Repo：{REPO_NAME}")
    print(f"完成後網址：https://{username}.github.io/{REPO_NAME}\n")

    # 確認 docs/ 存在並有 index.html
    DOCS_DIR.mkdir(exist_ok=True)
    if HTML_SRC.exists():
        import shutil
        shutil.copy(HTML_SRC, INDEX_DEST)
        print(f"✅ 複製 dashboard.html → docs/index.html")
    else:
        INDEX_DEST.write_text("<h1>儀表板尚未生成，請先執行 python3 main.py --scrape</h1>", encoding="utf-8")

    # 初始化 git
    if not (project / ".git").exists():
        _run(["git", "init", "-b", "main"], cwd=project)
        print("✅ git init")
    else:
        print("✅ git 已初始化")

    # 建立 .gitignore
    gitignore = project / ".gitignore"
    ignore_content = "\n".join([
        ".env",
        "state.db",
        "__pycache__/",
        "*.pyc",
        ".DS_Store",
        "dashboard.log",
        "refresh_dcard.py",
        "*.plist",
    ])
    gitignore.write_text(ignore_content + "\n", encoding="utf-8")
    print("✅ .gitignore 已建立（.env 和 state.db 不會上傳）")

    # 安裝 GitHub CLI
    result = subprocess.run(["which", "gh"], capture_output=True)
    if result.returncode != 0:
        print("\n⚠ 請先安裝 GitHub CLI：")
        print("  brew install gh")
        print("\n安裝後重新執行：python3 deploy.py --setup --username", username)
        return False

    # gh 登入
    auth_result = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
    if "Logged in" not in auth_result.stdout and "Logged in" not in auth_result.stderr:
        print("\n需要登入 GitHub CLI，執行：")
        subprocess.run(["gh", "auth", "login"])

    # 建立 GitHub repo（若已存在會跳過）
    print(f"\n建立 GitHub repo：{username}/{REPO_NAME}…")
    create_result = subprocess.run(
        ["gh", "repo", "create", REPO_NAME, "--public", "--source=.", "--remote=origin"],
        cwd=project, capture_output=True, text=True
    )
    if create_result.returncode == 0:
        print(f"✅ Repo 建立成功")
    else:
        # repo 可能已存在，嘗試 set remote
        print(f"（Repo 可能已存在，嘗試設定 remote…）")
        subprocess.run(
            ["git", "remote", "add", "origin",
             f"https://github.com/{username}/{REPO_NAME}.git"],
            cwd=project, capture_output=True
        )

    # 首次 commit + push
    _run(["git", "add", "docs/", ".gitignore", "*.py", "*.txt", "*.md"], cwd=project)
    _run(["git", "commit", "-m", "init: Garena 台灣玩家聲音儀表板"], cwd=project)
    push_result = _run(["git", "push", "-u", "origin", "main"], cwd=project, check=False)

    if push_result.returncode != 0:
        print("\n⚠ Push 失敗，可能需要先設定 git 身份：")
        print(f'  git config --global user.email "your@email.com"')
        print(f'  git config --global user.name "Your Name"')
        return False

    # 啟用 GitHub Pages（從 docs/ 目錄）
    print("\n啟用 GitHub Pages…")
    pages_result = subprocess.run(
        ["gh", "api", f"repos/{username}/{REPO_NAME}/pages",
         "--method", "POST",
         "--field", "source[branch]=main",
         "--field", "source[path]=/docs"],
        cwd=project, capture_output=True, text=True
    )
    if pages_result.returncode == 0:
        print(f"✅ GitHub Pages 已啟用")
    else:
        print(f"（Pages 可能需要在 GitHub 網頁手動啟用，或已啟用）")

    # 儲存 username 供日後 deploy 使用
    config_file = Path(__file__).parent / ".github_config"
    config_file.write_text(f"username={username}\nrepo={REPO_NAME}\n", encoding="utf-8")

    url = f"https://{username}.github.io/{REPO_NAME}"
    print(f"\n🎉 設定完成！")
    print(f"   儀表板網址：{url}")
    print(f"   （GitHub Pages 首次部署約需 1-2 分鐘才會生效）")
    print(f"\n之後每次執行 python3 main.py --scrape 都會自動更新網站。\n")

    # 儲存 URL 到 .env
    _write_env("DASHBOARD_URL", url)
    return True


def deploy() -> bool:
    """每次生成 dashboard.html 後自動推送更新。"""
    if not HTML_SRC.exists():
        print("[deploy] dashboard.html 不存在，跳過")
        return False

    config_file = Path(__file__).parent / ".github_config"
    if not config_file.exists():
        print("[deploy] 尚未設定 GitHub，跳過（執行 python3 deploy.py --setup --username YOUR_USERNAME）")
        return False

    # 讀取設定
    config = {}
    for line in config_file.read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            config[k.strip()] = v.strip()
    username = config.get("username", "")
    repo     = config.get("repo", REPO_NAME)
    if not username:
        return False

    # 複製最新 dashboard.html → docs/index.html
    import shutil
    DOCS_DIR.mkdir(exist_ok=True)
    shutil.copy(HTML_SRC, INDEX_DEST)

    project = Path(__file__).parent

    # git add + commit + push（包含程式碼變更）
    _run(["git", "add", "docs/", "*.py", "scrapers/", "processing/",
          "requirements.txt", ".env.example", "launchd/"], cwd=project)
    commit_result = _run(
        ["git", "commit", "-m", "auto: 更新儀表板"],
        cwd=project, check=False
    )
    if commit_result.returncode != 0:
        print("[deploy] 無變更，無需推送")
        return True  # 無變更也算成功

    push_result = _run(["git", "push", "origin", "main"], cwd=project, check=False)
    if push_result.returncode == 0:
        url = f"https://{username}.github.io/{repo}"
        print(f"[deploy] ✅ 儀表板已推送 → {url}")
        return True
    else:
        print("[deploy] ⚠ push 失敗，請檢查網路或 git 設定")
        return False


def get_dashboard_url() -> str:
    """取得儀表板 URL（供 email_sender 使用）。"""
    config_file = Path(__file__).parent / ".github_config"
    if not config_file.exists():
        return ""
    config = {}
    for line in config_file.read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            config[k.strip()] = v.strip()
    username = config.get("username", "")
    repo     = config.get("repo", REPO_NAME)
    return f"https://{username}.github.io/{repo}" if username else ""


def _run(cmd, cwd=None, check=True):
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.returncode != 0 and result.stderr.strip():
        print(result.stderr.strip())
    if check and result.returncode != 0:
        sys.exit(1)
    return result


def _write_env(key: str, value: str) -> None:
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    content = env_path.read_text(encoding="utf-8")
    import re
    new_line = f"{key}={value}"
    if re.search(rf"^{key}=", content, re.MULTILINE):
        content = re.sub(rf"^{key}=.*$", new_line, content, flags=re.MULTILINE)
    else:
        content += f"\n{new_line}\n"
    env_path.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="部署 Garena 儀表板到 GitHub Pages")
    parser.add_argument("--setup",    action="store_true", help="首次設定")
    parser.add_argument("--username", help="GitHub 帳號名稱（搭配 --setup 使用）")
    args = parser.parse_args()

    if args.setup:
        if not args.username:
            print("錯誤：--setup 需要搭配 --username YOUR_USERNAME")
            sys.exit(1)
        success = setup(args.username)
    else:
        success = deploy()

    sys.exit(0 if success else 1)
