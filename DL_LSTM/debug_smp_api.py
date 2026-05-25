"""
SMP API 429 응답 본문 확인용 디버그 스크립트
실행: python debug_smp_api.py
"""
import requests
from urllib.parse import urlencode
import config


def test_smp(date_str: str = '20250101', use_decoded_key: bool = True):
    """SMP API 단일 호출 테스트"""
    url = config.API_SMP_URL

    if use_decoded_key:
        # 디코딩 키 그대로 사용 (현재 방식)
        params = {
            'serviceKey': config.COMMON_API_KEY,
            'pageNo'    : 1,
            'numOfRows' : 100,
            'dataType'  : 'JSON',
            'baseDate'  : date_str,
        }
        r = requests.get(url, params=params, timeout=30)
    else:
        # URL 인코딩된 키 사용
        full_url = (f"{url}?serviceKey={config.COMMON_API_KEY}"
                    f"&pageNo=1&numOfRows=100&dataType=JSON&baseDate={date_str}")
        r = requests.get(full_url, timeout=30)

    print(f"\n{'=' * 60}")
    print(f"URL: {r.url}")
    print(f"HTTP Status: {r.status_code}")
    print(f"Content-Type: {r.headers.get('Content-Type')}")
    print(f"Content-Length: {r.headers.get('Content-Length')}")
    print(f"{'=' * 60}")
    print("응답 본문 (전체):")
    print(r.text[:3000])
    print(f"{'=' * 60}\n")

    # JSON 파싱 시도
    try:
        j = r.json()
        print("JSON 파싱 성공:")
        print(f"  최상위 키: {list(j.keys()) if isinstance(j, dict) else '(list)'}")
        if isinstance(j, dict) and 'response' in j:
            header = j.get('response', {}).get('header', {})
            print(f"  resultCode: {header.get('resultCode')}")
            print(f"  resultMsg : {header.get('resultMsg')}")
    except Exception as e:
        print(f"JSON 파싱 실패 (응답이 JSON이 아님): {e}")


if __name__ == '__main__':
    print("\n[테스트 1] 디코딩 키 + params dict")
    test_smp('20250101', use_decoded_key=True)

    print("\n[테스트 2] 키를 URL에 직접 삽입 (인코딩 안 함)")
    test_smp('20250101', use_decoded_key=False)
