"""Portable, escaped Markdown rendering for foreshadow planning reports."""
import html
import re


def _cell(value):
    text = html.escape(str(value if value is not None else '—'), quote=False)
    text = re.sub(r'([\\`*_{}\[\]()#+!|~])', r'\\\1', text)
    return text.replace('\r', ' ').replace('\n', ' / ')


def render_foreshadow_report(report):
    """No raw HTML, active Markdown links, or unescaped table delimiters."""
    lines = ['# Foreshadow planning report', '',
             f"Planning chapter: {report['current_chapter']}",
             f"Exported: {report['exported']} of {report['total_matches']} matching records.",
             'Complete: ' + ('yes' if report['complete'] else 'NO — increase max_items or narrow the filters.'),
             'Notes/evidence: ' + ('included' if report['include_notes'] else 'excluded'), '',
             'This is a current planning view, not a historical snapshot. Text and tags may contain private story details.', '',
             'Filters: ' + '; '.join(f'{key}={_cell(value)}' for key, value in report['filters'].items()), '',
             '| Foreshadow | Introduced | Target | Status | Due | Priority | Owner | Tags |',
             '| --- | --- | --- | --- | --- | --- | --- | --- |']
    for item in report['items']:
        row = [item.get('text'), item.get('introduced_chapter'), item.get('target_chapter'), item.get('status'),
               item.get('due_state'), item.get('priority'), 'author' if item.get('author_managed') else 'summary',
               ', '.join(item.get('tags', []))]
        lines.append('| ' + ' | '.join(_cell(value) for value in row) + ' |')
    if report['include_notes']:
        for item in report['items']:
            lines.extend(['', '## ' + _cell(item.get('text', '')), ''])
            for key in ('notes', 'evidence', 'resolution_note'):
                if item.get(key):
                    lines.append(f"- {key}: {_cell(item[key])}")
    return '\n'.join(lines) + '\n'
