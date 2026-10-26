"""Use the private key in-process; emit only status and neutral response samples."""

import json
from pathlib import Path
import sys

import requests


ROOT = Path(__file__).resolve().parents[1]
KEY_PATH = ROOT / '.local/renewal/credentials/deepseek.key'
SAMPLE_PATH = ROOT / 'tests/renewal/fixtures/deepseek-search-live.json'


def main() -> int:
    if not KEY_PATH.is_file():
        print(json.dumps({'status': 'key_not_configured'}))
        return 1
    try:
        key = KEY_PATH.read_text(encoding='utf-8').strip()
        response = requests.post(
            'https://api.deepseek.com/v1/tools/search',
            headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'},
            json={'query': 'Chrome extension development documentation', 'count': 5, 'need_content': False},
            timeout=20,
        )
        if response.status_code != 200:
            print(json.dumps({'status': 'api_error', 'http_status': response.status_code}))
            return 1
        payload = response.json()
        field = 'results' if isinstance(payload.get('results'), list) else 'raw' if isinstance(payload.get('raw'), list) else None
        if field is None:
            print(json.dumps({'status': 'unexpected_response_schema'}))
            return 1
        rows = payload[field]
        neutral_rows = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or not isinstance(row.get('title'), str) or not isinstance(row.get('url'), str):
                print(json.dumps({'status': 'unexpected_result_schema'}))
                return 1
            neutral = {}
            replacements = {'title': f'示例结果 {index + 1}', 'url': f'https://example.test/result/{index + 1}',
                            'snippet': '中性摘要，真实文本已替换。', 'summary': '中性摘要，真实文本已替换。',
                            'content': '', 'site_name': '示例来源', 'site_icon': 'https://example.test/icon.png'}
            for name, replacement in replacements.items():
                if name in row:
                    neutral[name] = None if row[name] is None else replacement
            for name in ('rerank_score', 'authority', 'date'):
                if name in row:
                    neutral[name] = row[name]
            neutral_rows.append(neutral)
        sample = {'fixture_kind': 'sanitized_live_response', 'query_kind': 'public_technical_documentation',
                  'observed_date': '2026-10-25', field: neutral_rows}
        SAMPLE_PATH.parent.mkdir(parents=True, exist_ok=True)
        SAMPLE_PATH.write_text(json.dumps(sample, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'status': 'success', 'response_field': field, 'result_count': len(rows),
                          'neutral_sample_written': True, 'key_printed': False}))
        return 0
    except (OSError, ValueError, requests.RequestException):
        print(json.dumps({'status': 'request_or_local_io_failed'}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
