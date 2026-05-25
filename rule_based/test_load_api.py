"""
공공데이터포털 ODcloud API 시스템의 부하 데이터 응답을 확인합니다.

URL: https://api.odcloud.kr/api/15065266/v1/uddi:6ade08d2-...

실행: python test_load_api.py
"""

import json
import requests
from urllib.parse import unquote
import config


def test_load_api():
    """부하 데이터 API 정확한 URL로 테스트"""
    print("=" * 70)
    print(" 한국전력거래소_시간별 전국 전력수요량 API 테스트 (2025년)")
    print("=" * 70)
    
    # ODcloud API URL (2025년 데이터)
    url = "https://api.odcloud.kr/api/15065266/v1/uddi:6ade08d2-0014-4d22-b10c-c811e3273c70"
    
    # ODcloud API의 표준 파라미터
    params = {
        'serviceKey': unquote(config.API_KEYS.get('smp', config.API_KEYS.get('kma'))),
        'page'      : 1,
        'perPage'   : 10,    # 처음에는 10개만 받기
        'returnType': 'JSON',
    }
    
    print(f"\n[요청 정보]")
    print(f"URL: {url}")
    print(f"파라미터: {params}\n")
    
    try:
        resp = requests.get(url, params=params, timeout=15)
        print(f"HTTP 상태: {resp.status_code}")
        print(f"응답 길이: {len(resp.text)} 글자")
        print(f"\n응답 (처음 1000자):")
        print("-" * 70)
        print(resp.text[:1000])
        print("-" * 70)
        
        print(f"\nJSON 파싱 시도:")
        try:
            data = resp.json()
            print("\n[전체 응답 구조]")
            print(json.dumps(data, indent=2, ensure_ascii=False)[:2500])
            
            # data 또는 items 확인
            print("\n[데이터 항목 추출 시도]")
            
            # 가능한 응답 구조들 시도
            items = None
            if 'data' in data:
                items = data['data']
                print(f"  → 'data' 키에서 발견")
            elif 'items' in data:
                items = data['items']
                print(f"  → 'items' 키에서 발견")
            elif 'response' in data:
                items = data.get('response', {}).get('body', {}).get('items', {})
                print(f"  → 'response.body.items' 에서 발견")
            
            if items:
                if isinstance(items, dict):
                    items = items.get('item', [])
                
                print(f"  항목 수: {len(items) if isinstance(items, list) else 'N/A'}")
                if isinstance(items, list) and len(items) > 0:
                    print(f"\n  첫 번째 항목:")
                    for key, value in items[0].items():
                        print(f"    {key}: {value}")
                    
                    if len(items) > 1:
                        print(f"\n  두 번째 항목:")
                        for key, value in items[1].items():
                            print(f"    {key}: {value}")
                
        except Exception as e:
            print(f"JSON 파싱 실패: {e}")
            
    except Exception as e:
        print(f"\n[ERROR] 요청 실패: {e}")
    
    print("\n" + "=" * 70)
    print("응답 결과를 알려주시면 정확한 코드를 만들어드립니다!")


if __name__ == '__main__':
    test_load_api()
