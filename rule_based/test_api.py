import json
import requests
from urllib.parse import unquote
import config


def test_load_api():
    """부하 데이터 API 테스트"""
    print("=" * 60)
    print(" 1. 한국전력거래소_시간별 전국 전력수요량 API 테스트")
    print("=" * 60)
    
    url = "http://apis.data.go.kr/B552115/PowerTradeInfoService/getTradeHourPwrQty"
    params = {
        'serviceKey': unquote(config.API_KEYS['load']),
        'pageNo'    : 1,
        'numOfRows' : 10,
        'dataType'  : 'json',
        'baseDate'  : '20250101',
    }
    
    try:
        resp = requests.get(url, params=params, timeout=10)
        print(f"\nHTTP 상태: {resp.status_code}")
        print(f"응답 길이: {len(resp.text)} 글자")
        print(f"\n응답 (처음 500자):")
        print(resp.text[:500])
        print("\n응답 (JSON 파싱 시도):")
        try:
            data = resp.json()
            print(json.dumps(data, indent=2, ensure_ascii=False)[:1000])
        except Exception as e:
            print(f"JSON 파싱 실패: {e}")
            print("→ XML 응답일 가능성")
    except Exception as e:
        print(f"\n[ERROR] 요청 실패: {e}")
    
    print("\n")


def test_smp_api():
    """SMP API 테스트"""
    print("=" * 60)
    print(" 2. 한국전력거래소_계통한계가격 및 수요예측 API 테스트")
    print("=" * 60)
    
    url = "http://apis.data.go.kr/B552115/SmpReqstStusService/getSmpReqstStusInfo"
    params = {
        'serviceKey': unquote(config.API_KEYS['smp']),
        'pageNo'    : 1,
        'numOfRows' : 10,
        'dataType'  : 'json',
        'baseDate'  : '20250101',
    }
    
    try:
        resp = requests.get(url, params=params, timeout=10)
        print(f"\nHTTP 상태: {resp.status_code}")
        print(f"응답 길이: {len(resp.text)} 글자")
        print(f"\n응답 (처음 500자):")
        print(resp.text[:500])
        print("\n응답 (JSON 파싱 시도):")
        try:
            data = resp.json()
            print(json.dumps(data, indent=2, ensure_ascii=False)[:1000])
        except Exception as e:
            print(f"JSON 파싱 실패: {e}")
    except Exception as e:
        print(f"\n[ERROR] 요청 실패: {e}")
    
    print("\n")


def test_kma_api():
    """기상청 ASOS API 테스트"""
    print("=" * 60)
    print(" 3. 기상청_지상(종관, ASOS) 시간자료 API 테스트")
    print("=" * 60)
    
    url = "http://apis.data.go.kr/1360000/AsosHourlyInfoService/getWthrDataList"
    params = {
        'serviceKey': unquote(config.API_KEYS['kma']),
        'pageNo'    : 1,
        'numOfRows' : 10,
        'dataType'  : 'JSON',
        'dataCd'    : 'ASOS',
        'dateCd'    : 'HR',
        'startDt'   : '20250101',
        'startHh'   : '00',
        'endDt'     : '20250101',
        'endHh'     : '23',
        'stnIds'    : 108,
    }
    
    try:
        resp = requests.get(url, params=params, timeout=15)
        print(f"\nHTTP 상태: {resp.status_code}")
        print(f"응답 길이: {len(resp.text)} 글자")
        print(f"\n응답 (처음 500자):")
        print(resp.text[:500])
        print("\n응답 (JSON 파싱 시도):")
        try:
            data = resp.json()
            print(json.dumps(data, indent=2, ensure_ascii=False)[:1500])
        except Exception as e:
            print(f"JSON 파싱 실패: {e}")
    except Exception as e:
        print(f"\n[ERROR] 요청 실패: {e}")
    
    print("\n")


if __name__ == '__main__':
    print("\n" + "=" * 60)
    print(" 공공데이터포털 API 응답 형식 진단")
    print("=" * 60)
    print(f"\n사용 키 (처음 20자): {config.API_KEYS['load'][:20]}...\n")
    
    test_load_api()
    test_smp_api()
    test_kma_api()
    
    print("=" * 60)
    print(" 진단 완료")
    print("=" * 60)
    print("\n위 응답 결과를 복사해서 알려주시면 코드를 정확히 수정해드립니다!")
