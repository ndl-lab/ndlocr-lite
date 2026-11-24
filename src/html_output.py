import html

from markdown_it import MarkdownIt


def build_html_document(document_name, markdown_pages, table_snippets=None):
    """Render OCR formatting without allowing OCR text to inject HTML."""
    renderer = MarkdownIt('commonmark', {'html': True, 'breaks': True})
    renderer.enable('table')
    renderer.disable('html_block')

    def render_inline_html(tokens, index, options, env):
        content = tokens[index].content
        return '<br>' if content == '<br>' else html.escape(content)

    renderer.renderer.rules['html_inline'] = render_inline_html
    title = html.escape(document_name)
    sections = [
        f'<section aria-label="Page {index}">\n{renderer.render(markdown)}</section>'
        for index, markdown in enumerate(markdown_pages, start=1)
    ]
    for token, snippet in (table_snippets or {}).items():
        # Only generated table HTML bypasses the renderer's markup escaping.
        sections = [
            section.replace(f'<p>{token}</p>', snippet)
            for section in sections
        ]
    return '\n'.join([
        '<!doctype html>',
        '<html lang="ja">',
        '<head>',
        '<meta charset="utf-8">',
        f'<title>{title}</title>',
        '<style>',
        'body{font-family:system-ui,sans-serif;margin:24px;line-height:1.6;}',
        'section+section{border-top:1px solid #aaa;margin-top:24px;padding-top:24px;}',
        'table{border-collapse:collapse;}',
        'td,th{border:1px solid #555;padding:4px 8px;vertical-align:top;}',
        '</style>',
        '</head>',
        '<body>',
        *sections,
        '</body>',
        '</html>',
    ])
