"""Inline SVG icons for the House Move portal, so the pages load nothing from outside the machine."""


class PortalIcons:  # pylint: disable=too-few-public-methods  # constants only
    """
    Stroke icons drawn in the current text color. Each is a complete <svg> element, marked aria-hidden: the text
    next to it carries the meaning.
    """

    HEAD: str = (
        '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round">'
    )
    HOME: str = HEAD + '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V20h5v-6h4v6h5V9.5"/></svg>'
    ROUTE: str = (
        HEAD + '<circle cx="6" cy="19" r="2"/><circle cx="18" cy="5" r="2"/>'
        '<path d="M8 19h7.5a3.5 3.5 0 0 0 0-7h-7a3.5 3.5 0 0 1 0-7H16"/></svg>'
    )
    UPLOAD: str = (
        HEAD
        + '<path d="M12 15V4"/><path d="m7 9 5-5 5 5"/><path d="M4 15v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4"/></svg>'
    )
    SIGN_OUT: str = (
        HEAD
        + '<path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3"/><path d="m10 17-5-5 5-5"/><path d="M5 12h11"/></svg>'
    )
    CHAT: str = HEAD + '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12Z"/></svg>'
    COPY: str = HEAD + '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a1 1 0 0 1 1-1h10"/></svg>'
    SHIELD: str = HEAD + '<path d="M12 3 5 6v5c0 4.5 3 8.3 7 10 4-1.7 7-5.5 7-10V6Z"/><path d="m9 12 2 2 4-4"/></svg>'
    USER: str = HEAD + '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></svg>'
    SEARCH: str = HEAD + '<circle cx="11" cy="11" r="6"/><path d="m20 20-4.2-4.2"/></svg>'
    CHECK: str = HEAD + '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/></svg>'
    FILE: str = (
        HEAD + '<path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8Z"/><path d="M14 3v5h5"/></svg>'
    )
