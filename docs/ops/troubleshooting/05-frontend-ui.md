# 프론트엔드·UI 트러블슈팅

Expo(React Native) 앱을 웹으로 서빙하며 겪은 문제.

**핵심 한 줄** — `react-native-web`은 네이티브 API의 일부를 조용히 무시한다. 실패가 예외가 아니라 **무반응**으로 나타난다.

---

## 1. ⚠️ `react-native-web`에는 `Alert`가 없다

### 증상

삭제 확인창이 웹에서 **아무 일도 일어나지 않았다.** 사용자는 삭제 기능이 고장난 줄 안다.

### 원인

`Alert.alert`는 react-native-web에서 구현되지 않았다. **예외도 경고도 없이 그냥 무시된다.**

### 해결

인앱 확인 UI로 대체했다. 타일 안에 오버레이를 띄우고 취소/삭제 버튼을 놓는다.

```tsx
{pendingDeleteId === item.id ? (
  <View style={styles.confirm}>
    <Text style={styles.confirmText}>삭제할까요?</Text>
    <Pressable onPress={() => setPendingDeleteId(null)}>취소</Pressable>
    <Pressable onPress={() => handleDelete(item.id)}>삭제</Pressable>
  </View>
) : null}
```

플랫폼 분기(`Platform.OS === 'web' ? window.confirm : Alert.alert`)도 가능하지만,
**인앱 UI가 모든 플랫폼에서 동일하게 동작하고 디자인 통제도 된다.**

---

## 2. `useNativeDriver`는 웹에서 경고만 남긴다

react-native-web에는 네이티브 드라이버가 없다. 켜두면 콘솔 경고가 쌓인다.

```ts
const useNativeDriver = Platform.OS !== 'web';

Animated.timing(pulse, { toValue: 1, duration: 700, useNativeDriver })
```

---

## 3. React 19에서 `react-test-renderer`는 `act()`가 필수다

### 증상

```
Can't access .root on unmounted test renderer
```

### 원인

React 19에서는 `act()`로 감싸지 않으면 **렌더가 커밋되지 않고 트리가 정리된다.**

### 해결

```tsx
function render(element: React.ReactElement): renderer.ReactTestRenderer {
  let tree!: renderer.ReactTestRenderer;
  renderer.act(() => {
    tree = renderer.create(element);
  });
  return tree;
}
```

상호작용도 `act()` 안에서 실행한다.

```tsx
function press(node: ReactTestInstance) {
  renderer.act(() => { node.props.onPress(); });
}
```

### 부수 팁

`@expo/vector-icons`는 jest 환경에서 폰트 로더를 해석하지 못한다. 모킹한다.

```tsx
jest.mock('@expo/vector-icons', () => ({ Ionicons: 'Ionicons' }));
```

테스트에서 요소를 찾을 때는 `accessibilityLabel`을 기준으로 하면 **접근성도 함께 챙겨진다.**

---

## 4. 입력 바 정렬 — 눈이 아니라 측정으로 잡는다

### 증상

"버튼이 좀 아래에 있다"는 피드백. 눈으로는 원인을 특정하기 어려웠다.

### 측정

브라우저에서 실제 좌표를 쟀다.

| 요소 | 높이 | 세로 중심 |
| --- | ---: | ---: |
| `+` 버튼 | 40px | 606 |
| 입력창 | **64px** | 594 |
| 전송 버튼 | 40px | 606 |

숫자를 보니 원인이 **두 개**였다.

1. `alignItems: 'flex-end'`라 버튼이 입력창 **아래쪽에 붙어** 있었다
2. 웹의 `multiline` textarea가 **기본 2줄**로 잡혀 입력창이 64px까지 부풀었다

### 해결

```tsx
inputRow: { alignItems: 'center' }        // flex-end → center
<TextInput multiline numberOfLines={1} /> // 한 줄에서 시작
```

### 결과

```
plus.cy = 265, input.cy = 265, send.cy = 265   → 편차 0px
입력창 높이 64px → 44px
```

### 교훈

> 레이아웃 어긋남은 **재서 원인을 특정한다.** 눈대중으로 값을 조정하면 증상만 가려진다.

```js
const r = el.getBoundingClientRect();
// x, y, width, height, 그리고 중심 좌표를 비교
```

---

## 5. 모바일 앱을 웹에서 열면 좌우로 늘어진다

### 증상

데스크톱 브라우저에서 입력 바의 좌우 버튼이 **화면 양 끝으로 벌어졌다.** 한 줄도 지나치게 길어 읽기 나빴다.

### 원인

모바일 기준으로 만든 레이아웃에 최대 폭 제약이 없었다.

### 해결

`ScreenContainer`에 최대 폭과 가운데 정렬을 넣어 **모든 탭을 한 번에** 통일했다.

```tsx
column: {
  width: '100%',
  alignSelf: 'center',
},
// maxWidth는 prop으로 주입 — 사이드바가 있는 화면만 넓게 쓴다
```

값은 `constants/layout.ts` 한 곳에서 관리한다.

> 스크롤 화면에서는 `contentContainerStyle`에 `alignSelf`를 주는 방법도 있으나 **플랫폼마다 동작이 다르다.**
> 안쪽 `View`로 감싸는 쪽이 안전했다.

하단 탭바는 컨테이너 바깥이라 **의도적으로 전체 폭을 유지**한다. 내용은 가운데, 네비게이션은 화면 끝까지.

---

## 6. 온보딩 썸네일이 안 뜬 이유 — `<Image>`가 없었다

### 증상

온보딩에서 사진을 올려도 썸네일이 표시되지 않았다.

### 원인

업로드 후 `uploaded.id`만 저장하고, **화면에는 `<Image>`가 아예 없었다.**
고정된 업로드 박스 4개의 라벨만 "사진 추가" → "추가됨"으로 바뀌고 있었다.

```tsx
// 문제 코드
{[0, 1, 2, 3].map((item) => (
  <ImageUploadBox title={item < closetImageIds.length ? '추가됨' : '사진 추가'} ... />
))}
```

### 해결

업로드 결과 전체(`UploadedImage`)를 보관하고 썸네일을 렌더링한다. 삭제와 최대 장수 제한도 함께 넣었다.

### 교훈

> "안 보인다"는 신고를 받으면 **렌더링 코드에 해당 요소가 있는지부터 확인한다.**
> 데이터 흐름을 의심하기 전에.

---

## 7. 내용 지문으로 중복 업로드 차단

### 목적

같은 사진을 두 번 올리는 것을 **업로드 전에** 걸러 네트워크와 서버 비용을 아낀다.

### 구현

Web Crypto로 SHA-256 지문을 계산한다. **백엔드가 저장 경로를 정할 때 쓰는 해시와 같아** 판정 기준이 어긋나지 않는다.

```ts
export async function getImageFingerprint(file: File): Promise<string> {
  if (typeof crypto === 'undefined' || !crypto.subtle) {
    // 보안 컨텍스트가 아니면 Web Crypto가 없다. 파일 속성으로 대체한다.
    return `${file.name}:${file.size}:${file.lastModified}`;
  }
  const buffer = await file.arrayBuffer();
  const digest = await crypto.subtle.digest('SHA-256', buffer);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, '0')).join('');
}
```

### 주의

- `crypto.subtle`은 **보안 컨텍스트(HTTPS 또는 localhost)에서만** 사용 가능하다. 폴백이 필요하다.
- 백엔드도 해시로 중복을 제거하므로 **지문이 달라도 같은 URL이 올 수 있다.** URL 기준으로 한 번 더 거른다.
- 지문 배열은 이미지 배열과 **같은 순서로 유지**해야 삭제 시 짝이 맞는다.

---

## 8. 파일 선택창의 취소를 감지할 수 없다

`<input type="file">`은 사용자가 아무것도 고르지 않고 닫으면 **`change` 이벤트가 발생하지 않는다.**
Promise가 영원히 대기 상태로 남는다.

창 포커스 복귀로 취소를 감지한다.

```ts
window.addEventListener('focus', () => {
  setTimeout(() => resolve(Array.from(input.files ?? [])), 300);
}, { once: true });
```

> 파일을 실제로 골랐다면 `input.files`가 이미 채워져 있으므로, 어느 경로로 resolve되든 결과는 같다.

---

## 체크리스트

- [ ] `Alert` 등 웹 미구현 API에 의존하고 있지 않은가
- [ ] `useNativeDriver`를 플랫폼 분기했는가
- [ ] React 19 테스트를 `act()`로 감쌌는가
- [ ] 레이아웃 문제를 **측정**으로 특정했는가
- [ ] 넓은 화면에서 최대 폭 제약이 있는가
- [ ] `crypto.subtle` 사용처에 폴백이 있는가
- [ ] 파일 선택 취소를 처리했는가

---

### 관련 문서

- [이미지 스토리지](./03-image-storage.md) — `accept` 속성과 지원 형식
- [AI 연동](./04-ai-integration.md) — 업로드 후 분석 요청 흐름
