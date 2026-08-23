# RAG 브랜치에 옮길 파일 정리

## 목표

`AID_project`에서 작업한 RAG 관련 코드만 `Backend` 저장소의 `rag` 브랜치로 옮긴다.

전체 프로젝트 폴더를 통째로 옮기지 않고, Backend 연결에 필요한 파일만 정리해서 추가한다.

---

## 현재 기준 핵심 상황

현재 GitHub `Backend` 저장소의 `app/services/rag_service.py`는 실제 ChromaDB 검색이 아니라 mock catalog 기반 임시 검색 코드다.

따라서 RAG 브랜치에서는 다음 작업이 필요하다.

```text
1. AID_project에서 만든 ChromaDB 기반 RAG 검색 로직을 Backend에 추가
2. 전처리/VectorDB 재생성 스크립트 추가
3. 필요한 데이터 또는 DB 폴더를 팀 정책에 맞게 포함하거나 별도 공유
4. Backend의 Agent가 호출하는 RAGService와 연결
```

---

## 옮길 파일

### 1. RAG 서비스 코드

현재 작업한 실제 RAG 검색 로직 파일.

```text
AID_project/rag_service_final.py
```

추천 위치:

```text
Backend/app/rag/rag_service.py
```

또는 기존 Backend 구조를 유지한다면:

```text
Backend/app/services/rag_service.py
```

역할:

```text
- RAGRequest 입력 처리
- query alias 확장
- ChromaDB 검색
- metadata score 계산
- musinsa / closet / hybrid 검색 처리
- RAGResponse 형식으로 반환
```

주의:

```text
- Backend의 기존 app/services/rag_service.py는 mock catalog 기반이므로 교체 또는 adapter 연결 필요
- item_type은 RAG 내부 검색/점수화에는 사용하지만, 현재 인터페이스 RAGItem에는 반환하지 않음
```

---

### 2. VLM 전처리 코드

`final_vlm_dataset`을 VectorDB/RAG용 JSON으로 바꾸는 스크립트.

```text
AID_project/preprocess_vlm.py
```

추천 위치:

```text
Backend/scripts/preprocess_vlm.py
```

역할:

```text
- final_vlm_dataset 로드
- 원본 VLM 필드 유지
- item_id 생성
- season_norm 생성
- 한영 alias가 포함된 search_document 생성
- preprocessed_vlm_dataset 저장
```

---

### 3. ChromaDB 구축 코드

전처리된 JSON을 ChromaDB에 저장하는 스크립트.

```text
AID_project/build_chromadb_final.py
```

추천 위치:

```text
Backend/scripts/build_chromadb.py
```

역할:

```text
- preprocessed_vlm_dataset 로드
- search_document 임베딩
- metadata 저장
- ChromaDB collection 생성
- collection count 검증
```

현재 기준:

```text
- DB 경로: chromadb_final
- collection 이름: musinsa
- embedding model: jhgan/ko-sroberta-multitask
```

---

### 4. Alias / 전처리 유틸 코드

현재 alias와 전처리 함수는 `preprocess_vlm.py`와 `rag_service_final.py` 안에 포함되어 있다.

Backend에 병합할 때는 유지보수를 위해 분리하는 것을 추천한다.

추천 파일:

```text
Backend/app/rag/aliases.py
Backend/app/rag/preprocessing.py
```

역할:

```text
- 검정/검은색/블랙 -> black
- 봄/간절기 -> spring
- 오버핏/박시한 -> oversized
- 하의/팬츠 -> 바지
- 붉은 계열 -> red / burgundy / pink 계열
```

주의:

```text
- 당장 빠른 병합이 목표라면 파일 분리 없이 rag_service.py 안에 포함해도 됨
- 최종 정리 단계에서는 aliases.py로 분리하는 쪽이 좋음
```

---

### 5. 문서화 파일

RAG 파이프라인 설명 문서.

추천 파일:

```text
Backend/docs/rag_pipeline.md
```

내용:

```text
- VLM 데이터 구조
- 전처리 방식
- ChromaDB 저장 구조
- document와 metadata의 차이
- alias 처리 방식
- RAG 요청/응답 구조
- 테스트 쿼리 예시
- 주의사항
```

---

## 조건부로 옮길 파일

### final_vlm_dataset

```text
AID_project/final_vlm_dataset/
```

추천 위치:

```text
Backend/data/final_vlm_dataset/
```

설명:

```text
- Gemini VLM으로 새로 받은 최종 데이터
- 전처리 재실행에 필요
- 데이터 용량과 팀 정책 확인 후 GitHub 포함 여부 결정
```

GitHub에 올리지 않는다면 별도 공유하고 `.gitignore`에 추가한다.

---

### preprocessed_vlm_dataset

```text
AID_project/preprocessed_vlm_dataset/
```

추천 위치:

```text
Backend/data/preprocessed_vlm_dataset/
```

설명:

```text
- final_vlm_dataset을 RAG/VectorDB용으로 전처리한 결과
- search_document에 한영 alias 포함
- ChromaDB 재생성에 바로 사용 가능
```

전처리 결과를 팀원도 바로 확인해야 한다면 올릴 수 있다.

---

### chromadb_final

```text
AID_project/chromadb_final/
```

추천 위치:

```text
Backend/data/chromadb_final/
```

설명:

```text
- 실제 검색에 사용하는 ChromaDB 저장 폴더
- 실행 시 바로 검색하려면 필요
- 다만 GitHub에 올리기에는 용량/바이너리 파일 관리 문제가 있을 수 있음
```

추천 판단:

```text
- 빠른 통합/시연이 목표: chromadb_final을 별도 압축 공유하거나 Git LFS 사용 검토
- 재현성 중심: chromadb_final은 올리지 않고 build_chromadb.py로 로컬 생성
```

---

## 옮기지 않을 파일

아래 파일/폴더는 GitHub에 올리지 않는 것을 추천한다.

```text
chromadb/
chromadb.zip
__pycache__/
*.pyc
.env
.env.local
crawling_dataset/
crawling_dataset.zip
vlm_dataset/
VLM_men_top_final_new.json
final_vlm_missing_report.csv
build_chromadb_errors.json
```

이유:

```text
- chromadb/는 예전 DB일 가능성이 있고 현재 기준은 chromadb_final
- zip 파일은 대용량
- __pycache__와 *.pyc는 실행 캐시
- .env는 민감 정보 포함 가능
- 기존 crawling/vlm 데이터는 현재 final 데이터 기준과 다름
- build_chromadb_errors.json은 현재 빈 검증 로그라 필수 산출물이 아님
```

---

## 추천 Backend 구조

```text
Backend/
├─ app/
│  ├─ rag/
│  │  ├─ __init__.py
│  │  ├─ rag_service.py
│  │  ├─ preprocessing.py
│  │  └─ aliases.py
│  │
│  └─ services/
│     └─ rag_service.py          # Agent가 호출하는 adapter 또는 기존 위치 교체
│
├─ scripts/
│  ├─ preprocess_vlm.py
│  └─ build_chromadb.py
│
├─ docs/
│  └─ rag_pipeline.md
│
└─ data/
   ├─ final_vlm_dataset/          # 선택
   ├─ preprocessed_vlm_dataset/   # 선택
   └─ chromadb_final/             # 선택 또는 별도 공유
```

---

## Git에 올릴 우선순위

### 필수

```text
app/rag/rag_service.py
app/rag/preprocessing.py
app/rag/aliases.py
scripts/preprocess_vlm.py
scripts/build_chromadb.py
docs/rag_pipeline.md
```

단, 빠른 병합을 위해 파일 분리를 하지 않는다면 최소 필수는 아래와 같다.

```text
app/services/rag_service.py
scripts/preprocess_vlm.py
scripts/build_chromadb.py
docs/rag_pipeline.md
```

### 선택

```text
data/final_vlm_dataset/
data/preprocessed_vlm_dataset/
data/chromadb_final/
```

### 제외

```text
chromadb/
chromadb.zip
.env
__pycache__/
기존 crawling_dataset/
기존 vlm_dataset/
```

---

## Backend 연결 체크리스트

```text
1. Backend의 app/services/rag_service.py가 더 이상 mock catalog를 반환하지 않는지 확인
2. Agent의 RAGRequest 스키마와 새 RAGService 입력이 맞는지 확인
3. RAGResponse가 app/schemas/ai.py의 RAGResponse / RAGItem을 통과하는지 확인
4. ChromaDB 경로가 Backend 실행 위치 기준으로 올바른지 확인
5. requirements.txt에 chromadb, sentence-transformers, torch 계열 의존성이 있는지 확인
6. item_type을 VLMItem 스키마에 추가할지 팀과 결정
7. retrieval_target closet / musinsa / hybrid 각각 최소 1회 테스트
```

---

## 최종 Git 작업 흐름

```powershell
cd C:\Users\andre\Desktop\Backend

git checkout rag

git status

git add app/rag app/services/rag_service.py scripts docs

git commit -m "feat: add ChromaDB RAG pipeline"

git push -u origin rag
```

데이터까지 올리기로 했다면:

```powershell
git add data/final_vlm_dataset data/preprocessed_vlm_dataset
git commit -m "chore: add RAG dataset"
git push
```

`chromadb_final`까지 올리기로 했다면 먼저 용량과 Git LFS 사용 여부를 확인한다.

```powershell
git add data/chromadb_final
git commit -m "chore: add ChromaDB index"
git push
```

---

## 현재 AID_project 기준으로 옮길 후보

```text
rag_service_final.py
preprocess_vlm.py
build_chromadb_final.py
preprocessed_vlm_dataset/
final_vlm_dataset/
chromadb_final/
```

현재 기준에서 가장 중요한 병합 대상은 `rag_service_final.py`와 `chromadb_final/`이다.

