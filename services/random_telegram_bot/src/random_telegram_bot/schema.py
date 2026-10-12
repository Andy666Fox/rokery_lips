"""Render reflected DB structure; distinguish legacy logical links from real FKs."""

import html
from collections.abc import Iterable

from sqlalchemy import MetaData, UniqueConstraint

LOGICAL_LINKS = (
    ("users", "telegram_id", "ratings", "telegram_id"),
    ("channels", "channel_id", "ratings", "channel_id"),
    ("users", "telegram_id", "subscribtions", "user_id"),
    ("channels", "channel_id", "users", "last_channel_id"),
)


def mermaid(metadata: MetaData) -> str:
    lines = [
        "erDiagram",
        "    %% Solid edges: enforced foreign keys. Dashed edges: logical legacy links.",
    ]
    for table in sorted(metadata.tables.values(), key=lambda t: t.name):
        lines.append(f"    {table.name} {{")
        for column in table.columns:
            kind = column.type.__class__.__name__.lower()
            key = " PK" if column.primary_key else " FK" if column.foreign_keys else ""
            lines.append(f"        {kind} {column.name}{key}")
        lines.append("    }")

    def cardinality(target: str, field: str) -> str:
        table = metadata.tables[target]
        column = table.c[field]
        unique = column.unique or any(
            isinstance(constraint, UniqueConstraint) and list(constraint.columns.keys()) == [field]
            for constraint in table.constraints
        )
        return "o|" if unique or (column.primary_key and len(table.primary_key) == 1) else "o{"

    for source, _, target, remote in physical_links(metadata):
        lines.append(f'    {source} ||--{cardinality(target, remote)} {target} : "FK"')
    for source, field, target, remote in LOGICAL_LINKS:
        if source in metadata.tables and target in metadata.tables:
            lines.append(
                f"    {source} ||..{cardinality(target, remote)} {target} : "
                f'"logical {field} to {remote}"'
            )
    return "\n".join(lines) + "\n"


def physical_links(metadata: MetaData) -> Iterable[tuple[str, str, str, str]]:
    for table in metadata.tables.values():
        for fk in sorted(table.foreign_keys, key=lambda key: key.parent.name):
            yield fk.column.table.name, fk.column.name, table.name, fk.parent.name


def svg(metadata: MetaData) -> str:
    tables = sorted(metadata.tables.values(), key=lambda t: t.name)
    width, gap, columns = 360, 80, 3
    row_height = max((len(t.columns) for t in tables), default=1) * 21 + 100
    positions = {
        t.name: (30 + (i % columns) * (width + gap), 70 + (i // columns) * row_height)
        for i, t in enumerate(tables)
    }
    height = 100 + ((len(tables) + columns - 1) // columns) * row_height
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="1320" height="{height}" '
        f'viewBox="0 0 1320 {height}">',
        '<rect width="100%" height="100%" fill="#101820"/>',
        "<style>text{font:14px monospace;fill:#edf4fa}"
        ".title{font-weight:bold;font-size:17px}</style>",
        '<text x="30" y="30">Database ER — solid: enforced FK; '
        "dashed: logical legacy relation</text>",
    ]
    links = [(a, b, c, d, False) for a, b, c, d in physical_links(metadata)]
    links += [(a, b, c, d, True) for a, b, c, d in LOGICAL_LINKS]
    for source, _, target, _, logical in links:
        if source not in positions or target not in positions:
            continue
        x1, y1 = positions[source]
        x2, y2 = positions[target]
        dash = ' stroke-dasharray="7 5"' if logical else ""
        parts.append(
            f'<path d="M{x1 + width / 2},{y1 + 15} L{x2 + width / 2},{y2 + 15}" '
            f'stroke="#56b5cb" stroke-width="2"{dash}/>'
        )
    for table in tables:
        x, y = positions[table.name]
        parts.append(
            f'<rect x="{x}" y="{y}" width="{width}" '
            f'height="{len(table.columns) * 21 + 50}" rx="8" fill="#1e2b38" stroke="#56b5cb"/>'
        )
        parts.append(
            f'<text x="{x + 12}" y="{y + 25}" class="title">{html.escape(table.name)}</text>'
        )
        for i, column in enumerate(table.columns):
            key = "PK " if column.primary_key else "FK " if column.foreign_keys else "   "
            label = f"{key}{column.name}: {column.type}" + ("?" if column.nullable else "")
            parts.append(f'<text x="{x + 12}" y="{y + 52 + i * 21}">{html.escape(label)}</text>')
    return "\n".join([*parts, "</svg>"])
