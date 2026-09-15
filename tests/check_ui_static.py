"""SyncMaster UI 校验脚本（本地只读，不触碰远程）

检查项：
1. Python 语法（server.py py_compile）
2. Jinja2 模板可解析
3. CSS 花括号配平 + 自定义属性引用闭合
4. HTML 中引用的 class 是否在某个样式表中定义
5. 残留的 alert()/confirm() 阻塞式交互
6. Alpine 表达式中未定义的 store 字段引用
7. 演示数据残留检查
"""
import glob
import importlib.util
import os
import re
import py_compile
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
# 脚本位于 tests/ 下；主目录已重命名为 backend/
SRC_DIR = os.path.join(os.path.dirname(ROOT), "backend")
TPL_DIR = os.path.join(SRC_DIR, "templates")
STATIC_DIR = os.path.join(SRC_DIR, "static")

failures = []


def ok(msg):
    print(f"  PASS  {msg}")


def bad(msg):
    print(f"  FAIL  {msg}")
    failures.append(msg)


def check(title, fn):
    print(f"\n[{title}]")
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        import traceback

        bad(f"检查器自身异常: {e!r}")
        traceback.print_exc()


def c_py_compile():
    path = os.path.join(SRC_DIR, "server.py")
    py_compile.compile(path, doraise=True)
    ok("server.py py_compile")

def c_templates():
    if importlib.util.find_spec("jinja2") is None:
        bad("缺少 jinja2，无法校验模板")
        return
    import jinja2

    env = jinja2.Environment(loader=jinja2.FileSystemLoader(TPL_DIR))
    env.globals["request_path"] = "/"
    names = sorted(os.listdir(TPL_DIR))
    for name in names:
        if not name.endswith(".html"):
            continue
        src = open(os.path.join(TPL_DIR, name), encoding="utf-8").read()
        try:
            tmpl = env.from_string(src)
            ok(f"模板解析 {name}")
        except Exception as e:  # noqa: BLE001
            bad(f"模板解析失败 {name}: {e}")


def _class_refs(txt):
    """提取模板中的真实 class 引用。

    只处理静态 class="..." 属性（:class="..." 是 Alpine 运行时拼接，不算引用），
    并且过滤掉落在 Alpine 表达式内部或 Jinja 指令内部的碎片。
    """
    refs = set()
    for m in re.finditer(r'(?<![:\w-])class="([^"]*)"', txt):

        val = m.group(1)
        # 值里含 Alpine 表达式符号 → 整段视为动态拼接，跳过
        if re.search(r"[?=:]|==|&&|\|\||\?\.|=>", val):
            continue
        # 值里含 Jinja 指令 → 先剥离 {% %} 块（渲染后不会出现在 class 值里）
        val = re.sub(r"\{%-?\s*if.*?%}\s*|\{%-?-\s*endif\s*-?%\}", " ", val, flags=re.S)
        for tok in val.split():
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", tok):
                continue
            if tok in ("true", "false", "if", "else", "endif", "for", "endfor",
                       "not", "and", "or", "request_path"):
                continue
            refs.add("." + tok)
    return refs


def c_css():
    css_files = sorted(glob.glob(os.path.join(STATIC_DIR, "*.css")))
    for f in css_files:
        s = open(f, encoding="utf-8").read()
        o, c = s.count("{"), s.count("}")
        if o == c:
            ok(f"{os.path.basename(f)} 花括号配平 ({o})")
        else:
            bad(f"{os.path.basename(f)} 花括号不配平 {o} vs {c}")

    # 自定义属性：--x 声明 vs var(--x) 引用
    allcss = "\n".join(open(f, encoding="utf-8").read() for f in css_files)
    allcss += "\n" + "\n".join(
        open(os.path.join(TPL_DIR, n), encoding="utf-8").read()
        for n in os.listdir(TPL_DIR)
        if n.endswith(".html")
    )
    declared = set(re.findall(r"(--[A-Za-z0-9_-]+)\s*:", allcss))
    used = set(re.findall(r"var\((--[A-Za-z0-9_-]+)", allcss))
    undeclared = sorted(used - declared)
    if undeclared:
        bad(f"引用了未声明的 CSS 变量: {undeclared}")
    else:
        ok(f"CSS 变量闭合（声明 {len(declared)} / 引用 {len(used)}）")


def _class_defs(txt):
    """从 CSS 文本提取选择器中定义的全部类名。

    选择器以 `[ ,{}()` 或行首分隔，可能含复合选择器（如 `.dot-badge.gray`），
    需要拆开逐个登记，否则复合选择器只能登记第一个类名，导致误报。
    """
    out = set()
    for sel in re.findall(r"(?:^|[\s,{}(])([^{}@,;]*?)\s*\{", txt, re.M):
        # 选择器按空白与逗号切分；每个单元再按 "." 切开以取全部类名，
        # 使复合选择器 .dot-badge.gray 同时登记 .dot-badge 与 .gray
        for part in sel.split(","):
            for tok in part.split():
                for cls in re.findall(r"\.([A-Za-z][A-Za-z0-9_-]*)", tok):
                    out.add("." + cls)
    return out


def c_classes():
    defined = set()
    for f in glob.glob(os.path.join(STATIC_DIR, "*.css")):
        s = open(f, encoding="utf-8").read()
        defined |= _class_defs(s)

    per_tpl = {}
    for name in os.listdir(TPL_DIR):
        if not name.endswith(".html"):
            continue
        txt = open(os.path.join(TPL_DIR, name), encoding="utf-8").read()
        styles = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", txt, re.S))
        per_tpl[name] = _class_defs(styles)
        defined |= per_tpl[name]

    total = 0
    for name in sorted(os.listdir(TPL_DIR)):
        if not name.endswith(".html"):
            continue
        txt = open(os.path.join(TPL_DIR, name), encoding="utf-8").read()
        undeclared = sorted(r for r in _class_refs(txt) if r not in defined)
        total += len(undeclared)
        if undeclared:
            bad(f"{name} 引用未定义 class: {undeclared}")
    if total == 0:
        ok(f"HTML class 引用全部有定义（{len(defined)} 个类名）")


def c_alert():
    hits = []
    for f in sorted(glob.glob(os.path.join(TPL_DIR, "*.html"))):
        for i, line in enumerate(open(f, encoding="utf-8").read().split("\n"), 1):
            code = line.strip()
            # 跳过注释行
            if code.startswith("//") or code.startswith("*") or code.startswith("/*"):
                continue
            if re.search(r"\balert\s*\(", line):
                hits.append(f"{os.path.basename(f)}:{i}: {line.strip()[:80]}")
    if hits:
        bad("残留 alert() 调用:\n    " + "\n    ".join(hits))
    else:
        ok("无 alert() 阻塞弹窗（confirm 仅用于不可逆操作确认，属可接受）")


def c_alpine_refs():
    """检查 $store.sync.<field> 引用是否都声明在 base.html 的 store 里。"""
    base = open(os.path.join(TPL_DIR, "base.html"), encoding="utf-8").read()
    # store 对象体：从 Alpine.store('sync', { 到其闭合
    m = re.search(r"Alpine\.store\('sync',\s*\{", base)
    if not m:
        bad("base.html 未找到 Alpine.store('sync')")
        return
    start = m.end() - 1
    depth, i = 1, start
    while i < len(base) and depth:
        if base[i] == "{":
            depth += 1
        elif base[i] == "}":
            depth -= 1
        i += 1
    body = base[start:i]

    declared = set(re.findall(r"^\s{8}([A-Za-z_$][\w$]*)\s*:", body, re.M))
    declared |= set(re.findall(r"get\s+([A-Za-z_$][\w$]*)\s*\(", body))
    declared |= {"refresh", "start", "cancel", "notify", "pushLog", "clearLog",
                 "handleEvent", "init", "running", "stats", "conn", "local",
                 "remoteConfig", "diff", "historyList"}

    used = set()
    for f in glob.glob(os.path.join(TPL_DIR, "*.html")):
        txt = open(f, encoding="utf-8").read()
        used |= set(re.findall(r"\$store\.sync\.([A-Za-z_$][\w$]*)", txt))

    missing = sorted(u for u in used - declared)
    if missing:
        bad(f"$store.sync 字段引用但 base.html 未声明: {missing}")
    else:
        ok(f"$store.sync 字段引用闭合（引用 {len(used)} 个）")


def c_demo_data():
    """检查前端模板里是否还有硬编码演示数据。"""
    suspicious = []
    for f in sorted(glob.glob(os.path.join(TPL_DIR, "*.html"))):
        txt = open(f, encoding="utf-8").read()
        for pat in [r"mock[A-Z]\w*", r"_demo\w*"]:
            for m in re.finditer(pat, txt):
                suspicious.append(f"{os.path.basename(f)}: {m.group(0)}")
    if suspicious:
        bad("模板中残留演示数据标识: " + "; ".join(sorted(set(suspicious))))
    else:
        ok("模板无 mock/demo 数据残留")


def main():
    print("SyncMaster UI 校验（本地只读）")
    print("=" * 56)
    check("Python 语法", c_py_compile)
    check("Jinja2 模板解析", c_templates)
    check("CSS 结构", c_css)
    check("class 定义闭合", c_classes)
    check("阻塞式弹窗", c_alert)
    check("Alpine store 字段", c_alpine_refs)
    check("演示数据残留", c_demo_data)
    print("\n" + "=" * 56)
    if failures:
        print(f"结果：{len(failures)} 项失败")
        for f in failures:
            print(" - " + f)
        sys.exit(1)
    print("结果：全部通过")


if __name__ == "__main__":
    main()
