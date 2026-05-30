import json
import requests
import config

url = config.API_LOAD_URL
params = {
    'page'      : 1,
    'perPage'   : 5,
    'serviceKey': config.COMMON_API_KEY,
}

print("=" * 60)
print(f"URL: {url}")
print(f"params: {params}")
print("=" * 60)

r = requests.get(url, params=params, timeout=30)
print(f"\nHTTP Status: {r.status_code}")
print(f"Response Headers Content-Type: {r.headers.get('Content-Type')}")

try:
    j = r.json()
    print(f"\n응답 최상위 키: {list(j.keys())}")
    print(f"totalCount: {j.get('totalCount')}")
    print(f"currentCount: {j.get('currentCount')}")
    print(f"data 길이: {len(j.get('data', []))}")

    data = j.get('data', [])
    if data:
        print("\n" + "=" * 60)
        print("첫 번째 행 (전체 키와 값):")
        print("=" * 60)
        first = data[0]
        for k, v in first.items():
            print(f"  {repr(k):<40} = {repr(v)}")

        print("\n" + "=" * 60)
        print("두 번째 행:")
        print("=" * 60)
        if len(data) > 1:
            for k, v in data[1].items():
                print(f"  {repr(k):<40} = {repr(v)}")
    else:
        print("\n[!] data 가 비어있음")
        print("\n응답 전문:")
        print(json.dumps(j, ensure_ascii=False, indent=2)[:2000])

except Exception as e:
    print(f"\nJSON 파싱 실패: {e}")
    print("\n응답 본문 (앞 2000자):")
    print(r.text[:2000])
