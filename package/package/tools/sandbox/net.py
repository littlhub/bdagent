# -*- coding: utf-8 -*-
"""联网抓取工具：fetch（网页正文）与 search（搜索）。

设计取舍：
  · 只用标准库 urllib，不引入第三方依赖
  · 抓回来的 HTML 剥成纯文本，去掉 script/style，压掉空白
  · 明确区分「网络不通 / 404 / 超时」等失败原因，模型据此决定下一步
  · 搜索走 DuckDuckGo 的 HTML 端点，不需要 API Key

沙箱约束：这两个工具只读网络，不写本地文件，因此不受 work 目录限制。
"""

from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

MAX_BYTES = 2 * 1024 * 1024


def _decode(raw, content_type=""):
    """按响应头/内容判断编码，默认 utf-8，失败退 gbk。"""
    enc = "utf-8"
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    if m:
        enc = m.group(1)
    for candidate in (enc, "utf-8", "gbk"):
        try:
            return raw.decode(candidate)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _strip_html(html):
    """HTML → 纯文本：去脚本样式、去标签、压实空白。"""
    text = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?is)<!--.*?-->", " ", text)
    # 导航/页脚/筛选器下拉不是正文，不删会把正文挤出截断窗口
    text = re.sub(r"(?is)<(nav|header|footer|aside|details|select)\b[^>]*>.*?</\1>", " ", text)
    # 块级标签转换行，行内标签去空
    text = re.sub(r"(?i)<(br|/p|/div|/li|/tr|/h[1-6])[^>]*>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    # 用标准库解实体，能覆盖 &ensp; &#0183; &amp; 等全部情况
    try:
        import html as _html
        text = _html.unescape(text)
    except Exception:
        for ent, ch in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                        ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")):
            text = text.replace(ent, ch)
    # \u2002/\u2003/\u2009 等排版空格统一成普通空格
    text = re.sub(r"[\u2000-\u200b\u00a0\u3000]+", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def _request(url, timeout=30):
    """发 GET，返回 (ok, text_or_error, final_url)。

    读取阶段容错：服务器提前断开（http.client.IncompleteRead）时，
    已收到的数据通常足够解析，不应整体判失败。bing 的搜索结果页
    实测经常触发这个错误。
    """
    import http.client

    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    resp = None
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        ctype = resp.headers.get("Content-Type", "")
        final = resp.geturl()
        try:
            raw = resp.read(MAX_BYTES)
        except http.client.IncompleteRead as e:
            # 用已读到的部分继续，只要够长就当成功
            raw = e.partial or b""
            if len(raw) < 2048:
                return False, f"响应不完整（仅 {len(raw)} 字节）", url
        return True, _decode(raw, ctype), final
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code} {e.reason}", url
    except urllib.error.URLError as e:
        return False, f"网络错误：{e.reason}", url
    except socket.timeout:
        return False, f"超时（>{timeout}s）", url
    except http.client.IncompleteRead as e:
        raw = e.partial or b""
        if len(raw) >= 2048:
            return True, _decode(raw, ""), url
        return False, f"响应不完整（仅 {len(raw)} 字节）", url
    except Exception as e:
        return False, f"{type(e).__name__}: {e}", url
    finally:
        try:
            if resp is not None:
                resp.close()
        except Exception:
            pass


def cmd_fetch(url, max_chars=8000):
    """抓取一个网页并返回可读正文。"""
    if not re.match(r"^https?://", url or "", re.I):
        return {"ok": False, "error": f"只支持 http/https，收到：{url!r}"}
    ok, text, final = _request(url)
    if not ok:
        return {"ok": False, "error": text, "url": url}
    body = _strip_html(text)
    truncated = len(body) > max_chars
    if truncated:
        body = body[:max_chars] + "\n...(已截断)"
    return {"ok": True, "url": final, "chars": len(body), "content": body}


def _parse_bing(html, limit):
    """解析 Bing 搜索结果页（cn.bing.com）。"""
    results = []
    # 每条结果：<li class="b_algo"> ... <h2><a href="...">标题</a></h2> ... <p>摘要</p>
    for block in re.findall(r'(?is)<li[^>]+class="[^"]*b_algo[^"]*"[^>]*>(.*?)</li>', html):
        m = re.search(r'(?is)<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block)
        if not m:
            continue
        href = m.group(1)
        title = _strip_html(m.group(2))
        sn = re.search(r"(?is)<p[^>]*>(.*?)</p>", block)
        snippet = _strip_html(sn.group(1)) if sn else ""
        if href.startswith("http"):
            results.append({"title": title, "url": href, "snippet": snippet})
        if len(results) >= limit:
            break
    return results


def _parse_sogou(html, limit):
    """解析搜狗搜索结果页（作为 Bing 不可用时的备选）。"""
    results = []
    for block in re.findall(r'(?is)<div[^>]+class="[^"]*vrwrap[^"]*"[^>]*>(.*?)</div>\s*</div>', html):
        m = re.search(r'(?is)<h3[^>]*>.*?<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block)
        if not m:
            continue
        href = m.group(1)
        title = _strip_html(m.group(2))
        sn = re.search(r'(?is)class="[^"]*(?:text-layout|str_info|space-txt)[^"]*"[^>]*>(.*?)</', block)
        snippet = _strip_html(sn.group(1)) if sn else ""
        if href.startswith("http") and title:
            results.append({"title": title, "url": href, "snippet": snippet})
        if len(results) >= limit:
            break
    return results


def _parse_toutiao(html, limit):
    """解析今日头条搜索结果。

    头条是 SPA，结果内嵌在页面 JSON 里（"title":"..." / "abstract":"..."），
    没有稳定的 DOM 结构，直接从 JSON 字段取比解析 HTML 可靠。
    """
    results = []
    seen = set()
    # title 与 abstract 在同一个结果对象里，按出现顺序配对
    titles = re.findall(r'"title"\s*:\s*"((?:[^"\\]|\\.){6,120})"', html)
    abstracts = re.findall(r'"abstract"\s*:\s*"((?:[^"\\]|\\.){10,300})"', html)
    urls = re.findall(r'"url"\s*:\s*"(https?:\\/\\/[^"]{10,200})"', html)
    # source_url 也可能是真实链接
    urls += re.findall(r'"source_url"\s*:\s*"(https?:\\/\\/[^"]{10,200})"', html)

    def unesc(s):
        # 头条把高亮词写成 \u003cem\u003e关键词\u003c/em\u003e（JSON 里的 <em>）。
        # 只替换 \uXXXX，绝不能整体 unicode_escape —— 那会把中文变成乱码。
        s = re.sub(r"\\u([0-9a-fA-F]{4})",
                   lambda m: chr(int(m.group(1), 16)), s)
        s = s.replace("\\/", "/").replace('\\"', '"').replace("\\n", " ")
        return _strip_html(s).strip()

    for i, t in enumerate(titles):
        title = unesc(t)
        if not title or title in seen:
            continue
        seen.add(title)
        snippet = unesc(abstracts[i]) if i < len(abstracts) else ""
        url = unesc(urls[i]) if i < len(urls) else ""
        results.append({"title": title, "url": url, "snippet": snippet})
        if len(results) >= limit:
            break
    return results


def _parse_bilibili(html, limit):
    """解析 B 站搜索结果（服务端渲染的 video-list）。"""
    results = []
    for block in re.findall(r'(?is)<li[^>]+class="[^"]*video-item[^"]*"[^>]*>(.*?)</li>', html):
        m = re.search(r'(?is)<a[^>]+href="(//www\.bilibili\.com/video/[^"]+)"[^>]*title="([^"]{4,120})"', block)
        if not m:
            m = re.search(r'(?is)<a[^>]+href="(//www\.bilibili\.com/video/[^"]+)"[^>]*>(.*?)</a>', block)
        if not m:
            continue
        href = "https:" + m.group(1)
        title = _strip_html(m.group(2))
        if title:
            results.append({"title": title, "url": href, "snippet": ""})
        if len(results) >= limit:
            break
    return results


# 娱乐/明星类内容：官方媒体往往搜不到，要换社交与内容平台
_ENTERTAINMENT_HINTS = (
    "明星", "艺人", "演员", "歌手", "偶像", "行程", "通告", "演唱会",
    "综艺", "影视", "剧组", "粉丝", "八卦", "恋情", "官宣", "热搜",
    "绯闻", "路透", "机场", "红毯", "代言",
)


def is_entertainment_query(query):
    low = (query or "").lower()
    return any(w in low for w in _ENTERTAINMENT_HINTS)


def cmd_search(query, limit=8):
    """联网搜索。

    按内容类型选源：
      · 娱乐/明星类 → 今日头条、B站（官方媒体没有这类内容）
      · 其它 → Bing，失败退搜狗

    注意：DuckDuckGo 在国内网络不可达，不要作为主源。
    """
    q = urllib.parse.quote_plus(query or "")

    if is_entertainment_query(query):
        sources = [
            ("toutiao", f"https://so.toutiao.com/search?keyword={q}", _parse_toutiao),
            ("bilibili", f"https://search.bilibili.com/all?keyword={q}", _parse_bilibili),
            ("bing", f"https://cn.bing.com/search?q={q}", _parse_bing),
        ]
    else:
        sources = [
            ("bing", f"https://cn.bing.com/search?q={q}", _parse_bing),
            ("sogou", f"https://www.sogou.com/web?query={q}", _parse_sogou),
        ]
    errors = []
    for name, url, parser in sources:
        ok, text, _ = _request(url, timeout=20)
        if not ok:
            errors.append(f"{name}: {text}")
            continue
        results = parser(text, limit)
        if results:
            return {"ok": True, "query": query, "source": name,
                    "count": len(results), "results": results}
        errors.append(f"{name}: 已连通但未解析到条目")

    return {"ok": False, "query": query,
            "error": "所有搜索源均失败：" + "；".join(errors)}
