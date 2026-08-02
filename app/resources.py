"""Loads resource articles from markdown files.

One file per article in app/content/resources/. Frontmatter drives the URL,
the title tag, the meta description and the index card. Files are re-read on
each request, so adding an article means copying a file -- no code, no restart.
"""
import os
import re
import frontmatter
import markdown as md

CONTENT_DIR = os.path.join(os.path.dirname(__file__), "content", "resources")

_MD = md.Markdown(extensions=["extra"])


def _one(path):
    post = frontmatter.load(path)
    meta = post.metadata
    slug = meta.get("slug") or os.path.splitext(os.path.basename(path))[0]
    _MD.reset()
    return {
        "slug": slug,
        "title": meta.get("title", slug.replace("-", " ").title()),
        "meta_title": meta.get("meta_title") or meta.get("title", ""),
        "meta_description": meta.get("meta_description", ""),
        "summary": meta.get("summary", ""),
        "order": int(meta.get("order", 100)),
        "status": meta.get("status", "published"),
        "body_html": _MD.convert(post.content),
    }


def all_articles(include_drafts=False):
    """Every published article, ordered."""
    if not os.path.isdir(CONTENT_DIR):
        return []
    out = []
    for name in sorted(os.listdir(CONTENT_DIR)):
        if not name.endswith(".md"):
            continue
        try:
            art = _one(os.path.join(CONTENT_DIR, name))
        except Exception as e:
            print("[resources] SKIPPED %s: %s" % (name, e))
            continue
        if art["status"] == "draft" and not include_drafts:
            continue
        out.append(art)
    out.sort(key=lambda a: (a["order"], a["title"]))
    return out


def get(slug):
    for a in all_articles():
        if a["slug"] == slug:
            return a
    return None


def apply_prefix(html, prefix):
    """Rewrite site-internal hrefs so they work under the draft /preview prefix.
    Leaves /static, absolute URLs, anchors, tel: and mailto: alone."""
    if not prefix:
        return html
    return re.sub(
        r'href="/(?!/|static/)',
        'href="%s/' % prefix,
        html,
    )
