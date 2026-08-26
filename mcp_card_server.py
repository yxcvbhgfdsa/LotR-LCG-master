import asyncio
from urllib.parse import quote_plus, urljoin

import httpx
from bs4 import BeautifulSoup
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool
import mcp.types as types

BASE_URL = 'https://hallofbeorn.com'
SEARCH_URL = f'{BASE_URL}/LotR/Search?Query={{query}}'


def clean_text(text):
    return ' '.join(text.split())


def parse_search_results(soup):
    cards = []
    result_divs = soup.select('div[id^="search-result-"]')
    for div in result_divs:
        link_tag = div.select_one('a[href*="/LotR/Details/"]')
        if not link_tag:
            continue

        name = clean_text(link_tag.get_text(' ', strip=True))
        detail_url = urljoin(BASE_URL, link_tag.get('href', ''))
        details = clean_text(div.get_text(' ', strip=True))
        if details.startswith('Score:'):
            parts = details.split('?', 1)
            details = clean_text(parts[1] if len(parts) > 1 else details)

        image_tag = div.find('img')
        image_url = ''
        if image_tag and image_tag.get('src'):
            image_url = urljoin(BASE_URL, image_tag['src'])

        cards.append({
            'name': name,
            'details': details,
            'detail_url': detail_url,
            'image_url': image_url,
        })
    return cards


async def handle_list_tools(_context, _params):
    tools = [
        Tool(
            name='search_cards',
            description='Search Hall of Beorn for Lord of the Rings LCG cards by keyword and return English card details with detail page links.',
            inputSchema={
                'type': 'object',
                'properties': {
                    'query': {
                        'type': 'string',
                        'description': "Card keyword, such as 'Gandalf' or 'Aragorn'",
                    }
                },
                'required': ['query'],
            },
        )
    ]
    return types.ListToolsResult(tools=tools)


async def handle_call_tool(_context, params: types.CallToolRequestParams):
    name = params.name
    arguments = params.arguments or {}
    if name != 'search_cards':
        return types.CallToolResult(
            content=[TextContent(type='text', text=f'Unknown tool: {name}')],
            is_error=True,
        )

    query = clean_text(str(arguments.get('query', '')))
    if not query:
        return types.CallToolResult(
            content=[TextContent(type='text', text='请提供要查询的卡牌关键词，例如 Gandalf 或 Aragorn。')],
            is_error=True,
        )

    url = SEARCH_URL.format(query=quote_plus(query))
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            response = await client.get(url, timeout=15.0)
            response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')
        cards = parse_search_results(soup)
        if not cards:
            return types.CallToolResult(
                content=[TextContent(type='text', text=f'无法找到对于 {query} 的 Hall of Beorn 搜索结果。')]
            )

        lines = [f'查到了 {len(cards)} 张相关卡牌：']
        for index, card in enumerate(cards, 1):
            lines.extend([
                '',
                f'{index}. 英文：{card["name"]}',
                f'英文详情：{card["details"]}',
                f'详情页：{card["detail_url"]}',
            ])
            if card['image_url']:
                lines.append(f'卡图：{card["image_url"]}')

        return types.CallToolResult(content=[TextContent(type='text', text='\n'.join(lines))])
    except Exception as e:
        return types.CallToolResult(
            content=[TextContent(type='text', text=f'Error: {str(e)}')],
            is_error=True,
        )


app = Server(
    'lotr-card-search',
    on_list_tools=handle_list_tools,
    on_call_tool=handle_call_tool,
)


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(),
        )


if __name__ == '__main__':
    asyncio.run(main())
