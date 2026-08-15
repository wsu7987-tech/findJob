from pathlib import Path

from bs4 import BeautifulSoup


def test_extract_main_content_prefers_article_fixture() -> None:
    from backend.app.services.web_capture.content_extractor import extract_rendered_document

    html = Path("backend/tests/fixtures/web/article.html").read_text(encoding="utf-8")
    extracted = extract_rendered_document(
        url="https://example.com/post",
        title="ignored",
        rendered_html=html,
        ocr_segments=[],
    )

    assert extracted.title == "Article Title"
    assert "# Article Title" in extracted.markdown_text
    assert "Sidebar link" not in extracted.raw_text
    assert "Lead paragraph" in extracted.raw_text
    assert extracted.preview_text.startswith("# Article Title")
    assert extracted.preview_pages[0].page_number == 1
    assert extracted.preview_pages[0].content_type == "markdown"


def test_extract_rendered_document_uses_title_tag_before_fallback_title() -> None:
    from backend.app.services.web_capture.content_extractor import extract_rendered_document

    html = """<!doctype html>
<html>
  <head>
    <title>Document Title</title>
  </head>
  <body>
    <p>Body text only.</p>
  </body>
</html>
"""

    extracted = extract_rendered_document(
        url="https://example.com/title",
        title="Fallback Title",
        rendered_html=html,
        ocr_segments=[],
    )

    assert extracted.title == "Document Title"
    assert extracted.markdown_text.startswith("# Document Title")
    assert "Body text only." in extracted.raw_text


def test_extract_rendered_document_uses_fallback_title_before_url() -> None:
    from backend.app.services.web_capture.content_extractor import extract_rendered_document

    html = """<!doctype html>
<html>
  <head></head>
  <body>
    <p>Body text only.</p>
  </body>
</html>
"""

    extracted = extract_rendered_document(
        url="https://example.com/fallback",
        title="Fallback Title",
        rendered_html=html,
        ocr_segments=[],
    )

    assert extracted.title == "Fallback Title"
    assert extracted.markdown_text.startswith("# Fallback Title")
    assert "Body text only." in extracted.raw_text


def test_extract_rendered_document_falls_back_to_url_when_title_missing() -> None:
    from backend.app.services.web_capture.content_extractor import extract_rendered_document

    html = """<!doctype html>
<html>
  <head></head>
  <body>
    <p>Body text only.</p>
  </body>
</html>
"""

    extracted = extract_rendered_document(
        url="https://example.com/url-fallback",
        title="",
        rendered_html=html,
        ocr_segments=[],
    )

    assert extracted.title == "https://example.com/url-fallback"
    assert extracted.markdown_text.startswith("# https://example.com/url-fallback")
    assert "Body text only." in extracted.raw_text
    assert extracted.preview_pages[0].page_number == 1
    assert extracted.preview_text.startswith("# https://example.com/url-fallback")


def test_extract_rendered_document_uses_login_wall_fixture() -> None:
    from backend.app.services.web_capture.content_extractor import extract_rendered_document

    html = Path("backend/tests/fixtures/web/login-wall.html").read_text(encoding="utf-8")
    extracted = extract_rendered_document(
        url="https://example.com/login-wall",
        title="ignored",
        rendered_html=html,
        ocr_segments=[],
    )

    assert extracted.title == "Sign in to continue"
    assert "short teaser" in extracted.raw_text
    assert extracted.preview_pages[0].page_number == 1
    assert extracted.preview_text.startswith("# Sign in to continue")


def test_extract_rendered_document_falls_back_to_div_section_content() -> None:
    from backend.app.services.web_capture.content_extractor import extract_rendered_document

    html = """<!doctype html>
<html>
  <head>
    <title>Div Page</title>
  </head>
  <body>
    <div class="article-body">First div paragraph. Second sentence.</div>
    <section>Section paragraph with supporting text.</section>
  </body>
</html>
"""

    extracted = extract_rendered_document(
        url="https://example.com/div-body",
        title="ignored",
        rendered_html=html,
        ocr_segments=[],
    )

    assert extracted.title == "Div Page"
    assert "First div paragraph." in extracted.markdown_text
    assert "Section paragraph" in extracted.raw_text
    assert extracted.preview_pages[0].page_number == 1


def test_extract_rendered_document_collects_mixed_heading_and_container_body() -> None:
    from backend.app.services.web_capture.content_extractor import extract_rendered_document

    html = """<!doctype html>
<html>
  <head>
    <title>Mixed Page</title>
  </head>
  <body>
    <article>
      <h1>Title</h1>
      <h2>Subhead</h2>
      <div>Summary</div>
      <div>real body from a div container</div>
      <section>more body from a section container</section>
    </article>
  </body>
</html>
"""

    extracted = extract_rendered_document(
        url="https://example.com/mixed",
        title="ignored",
        rendered_html=html,
        ocr_segments=[],
    )

    assert extracted.title == "Title"
    assert "Subhead" in extracted.markdown_text
    assert "Summary" in extracted.raw_text
    assert "real body from a div container" in extracted.raw_text
    assert "more body from a section container" in extracted.raw_text


def test_paginate_markdown_splits_by_markdown_blocks() -> None:
    from backend.app.services.web_capture.content_extractor import paginate_markdown

    first_block = "First block " + ("A" * 1200)
    second_block = "Second block " + ("B" * 1200)
    markdown_text = f"# Report\n\n{first_block}\n\n{second_block}"

    pages = paginate_markdown(markdown_text)

    assert len(pages) == 2
    assert pages[0].page_number == 1
    assert pages[0].content.startswith("# Report")
    assert first_block in pages[0].content
    assert pages[1].page_number == 2
    assert pages[1].content == second_block
    assert pages[0].content_type == "markdown"
    assert pages[1].content_type == "markdown"


def test_paginate_markdown_splits_single_long_block() -> None:
    from backend.app.services.web_capture.content_extractor import paginate_markdown

    long_block = ("A" * 1700) + "\n" + ("B" * 1700) + "\n" + ("C" * 1700)
    pages = paginate_markdown(long_block)

    assert len(pages) > 1
    assert all(len(page.content) <= 1800 for page in pages)
    assert "".join(page.content for page in pages) == long_block


def test_extract_rendered_document_preserves_preformatted_code_text() -> None:
    from backend.app.services.web_capture.content_extractor import extract_rendered_document

    html = """<!doctype html>
<html>
  <head>
    <title>Code Sample</title>
  </head>
    <body>
    <article>
      <h1>Code Sample</h1>
      <pre><code>print("hello")
- preserve me
> keep me
  indented = True</code></pre>
    </article>
  </body>
</html>
"""

    extracted = extract_rendered_document(
        url="https://example.com/code",
        title="ignored",
        rendered_html=html,
        ocr_segments=[],
    )

    assert "```" in extracted.markdown_text
    assert 'print("hello")' in extracted.markdown_text
    assert 'print("hello")' in extracted.raw_text
    assert "\n- preserve me" in extracted.raw_text
    assert "\n> keep me" in extracted.raw_text
    assert "\n  indented = True" in extracted.raw_text


def test_filter_ocr_candidates_filters_decorative_and_small_images() -> None:
    from backend.app.services.web_capture.image_ocr import filter_ocr_candidates

    html = Path("backend/tests/fixtures/web/image-heavy.html").read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")
    elements = [
        {
            "src": img.get("src"),
            "alt": img.get("alt"),
            "width": img.get("width"),
            "height": img.get("height"),
            "decorative": False,
        }
        for img in soup.find_all("img")
    ]
    elements.extend(
        [
            {"src": "/images/decorative.png", "width": 400, "height": 400, "decorative": True},
            {"src": "/images/tiny.png", "width": 32, "height": 32, "decorative": False},
            {"src": "/images/boundary.png", "width": 64, "height": 64, "decorative": False},
        ]
    )

    filtered = filter_ocr_candidates(elements)

    assert [item["src"] for item in filtered] == [
        "/images/hero.png",
        "/images/chart-a.png",
        "/images/boundary.png",
    ]


def test_merge_ocr_segments_appends_near_related_block() -> None:
    from backend.app.services.web_capture.content_extractor import merge_ocr_segments

    markdown_text = "# Report\n\nBefore chart\n\nAfter chart"
    merged = merge_ocr_segments(
        markdown_text=markdown_text,
        ocr_segments=[{"anchor_text": "Before chart", "text": "Revenue 42%"}],
    )

    assert "Revenue 42%" in merged
    assert merged.index("Before chart") < merged.index("Revenue 42%")
