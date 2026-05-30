import json
import requests
from urllib.parse import unquote
import config


def test_smp_api():
    """SMP API 정확한 URL로 테스트"""
    print("=" * 70)
    print(" 한국전력거래소_계통한계가격 및 수요예측 API 테스트")
    print("=" * 70)
    
    # Swagger에서 확인한 정확한 URL
    url = "https://apis.data.go.kr/B552115/SmpWithForecastDemand/getSmpWithForecastDemand"
    
    params = {
        'serviceKey': unquote(config.API_KEYS['smp']),
        'pageNo'    : 1,
        'numOfRows' : 24,
        'dataType'  : 'JSON',     # JSON으로 시도
        'date'      : '20250101',
    }
    
    print(f"\n[요청 정보]")
    print(f"URL: {url}")
    print(f"파라미터: {params}\n")
    
    try:
        resp = requests.get(url, params=params, timeout=10)
        print(f"HTTP 상태: {resp.status_code}")
        print(f"응답 길이: {len(resp.text)} 글자")
        print(f"\n응답 (처음 800자):")
        print("-" * 70)
        print(resp.text[:800])
        print("-" * 70)
        
        print(f"\nJSON 파싱 시도:")
        try:
            data = resp.json()
            print("\n[전체 응답 구조]")
            print(json.dumps(data, indent=2, ensure_ascii=False)[:2000])
            
            # items 확인
            print("\n[items 추출 시도]")
            try:
                items = data['response']['body']['items']['item']
                if isinstance(items, dict):
                    items = [items]
                print(f"  항목 수: {len(items)}")
                if len(items) > 0:
                    print(f"\n  첫 번째 항목:")
                    for key, value in items[0].items():
                        print(f"    {key}: {value}")
            except KeyError as e:
                print(f"  KeyError: {e}")
                print(f"  실제 구조: {list(data.keys())}")
                
        except Exception as e:
            print(f"JSON 파싱 실패: {e}")
            print("→ XML 응답일 수 있음")
            print("\nXML 시도:")
            print(resp.text[:1500])
            
    except Exception as e:
        print(f"\n[ERROR] 요청 실패: {e}")
    
    print("\n" + "=" * 70)


if __name__ == '__main__':
    test_smp_api()
    print("\n위 출력을 복사해서 보여주시면 정확한 코드를 만들어드립니다!")
