"""
HTML Template Module: Head Section

HTML head with meta tags, CDN imports (Vue 3, D3.js, Dagre, Tailwind).
"""


def get_head_section(escaped_repo_name: str) -> str:
    """Get HTML head with meta tags and CDN imports.

    Args:
        escaped_repo_name: HTML-escaped repository name
    """
    return f"""    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{escaped_repo_name} - PBIP Analysis</title>

    <!-- Google Fonts: Fraunces (display), DM Sans (body), IBM Plex Mono (code) -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600;9..144,700&family=DM+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">

    <!-- Vue 3, D3.js, and Dagre for graph layouts -->
    <script src="https://cdn.jsdelivr.net/npm/vue@3.4.21/dist/vue.global.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/dagre@0.8.5/dist/dagre.min.js"></script>
    <script src="https://cdn.tailwindcss.com"></script>
"""
