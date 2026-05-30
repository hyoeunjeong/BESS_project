### 설치
요구사항
Python 3.9 이상
pip

### 패키지 설치
pip install -r requirements_web.txt
pip install -r DL_LSTM\requirements.txt

### 환경변수 설정
.env 파일에 공공데이터포털에서 받은 api 직접 적용

1. 부하 데이터 API (ODcloud)
URL: https://www.data.go.kr/data/15065266/fileData.do

2. SMP API (Public Data Portal)
URL: https://www.data.go.kr/data/15131225/openapi.do

3. 기상청 ASOS API (Public Data Portal)
URL: https://www.data.go.kr/data/15057210/openapi.do

4. 기상청 단기예보 조회서비스 (Public Data Portal)
URL: https://www.data.go.kr/data/15084084/openapi.do


### 터미널 서버 실행

터미널 1
python realtime_engine.py

터미널 2
python web_app_realtime.py

### 접속

PC버전
http://localhost:5000/

Mobile 버전
http://localhost:5000/mobile

다른 기기
http://[서버IP]:5000/

###종료
각 터미널에서 Ctrl + C