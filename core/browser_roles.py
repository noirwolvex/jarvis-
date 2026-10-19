"""Roles supported by Playwright's semantic locator, shared by schemas and dispatch."""

BROWSER_ROLES = (
    "alert", "alertdialog", "application", "article", "banner", "blockquote", "button",
    "caption", "cell", "checkbox", "code", "columnheader", "combobox", "complementary",
    "contentinfo", "definition", "deletion", "dialog", "directory", "document", "emphasis",
    "feed", "figure", "form", "generic", "grid", "gridcell", "group", "heading", "img",
    "insertion", "link", "list", "listbox", "listitem", "log", "main", "marquee", "math",
    "menu", "menubar", "menuitem", "menuitemcheckbox", "menuitemradio", "meter", "navigation",
    "none", "note", "option", "paragraph", "presentation", "progressbar", "radio", "radiogroup",
    "region", "row", "rowgroup", "rowheader", "scrollbar", "search", "searchbox", "separator",
    "slider", "spinbutton", "status", "strong", "subscript", "superscript", "switch", "tab",
    "table", "tablist", "tabpanel", "term", "textbox", "time", "timer", "toolbar", "tooltip",
    "tree", "treegrid", "treeitem",
)


def validate_browser_role(target: dict) -> None:
    if "role" in target and target["role"] not in BROWSER_ROLES:
        raise ValueError(
            "Unsupported browser role; use an observed accessible role/name or exact selector. "
            "Plain page text is not role='text'; inspect it with browser_read_page."
        )
