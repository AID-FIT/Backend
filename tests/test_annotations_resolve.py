"""모든 모듈의 타입 애노테이션이 실제로 해석되는지 확인한다.

로컬은 Python 3.14다. PEP 649로 애노테이션이 지연 평가되므로, 정의되지 않은
이름을 애노테이션에 써도 import가 통과한다. 배포 환경(3.12)은 함수 정의
시점에 평가하므로 같은 코드가 NameError로 죽는다.

실제로 `catalog_matching`이 `Optional`을 import하지 않고 쓰다가 이 틈으로
새어 나갔다. 테스트 295개가 전부 통과하는데 프로덕션에서는 pgvector 검색이
통째로 실패했고, nodes.py가 예외를 삼켜 로그에도 남지 않았다.
"""

import importlib
import inspect
import pkgutil
import typing

import pytest

import app


def app_modules() -> list[str]:
    return sorted(
        module.name
        for module in pkgutil.walk_packages(app.__path__, prefix="app.")
        if not module.ispkg
    )


@pytest.mark.parametrize("module_name", app_modules())
def test_annotations_resolve_on_older_pythons(module_name: str) -> None:
    module = importlib.import_module(module_name)

    unresolved: list[str] = []
    for name, member in vars(module).items():
        if getattr(member, "__module__", None) != module_name:
            continue
        targets = [member] if callable(member) else []
        if inspect.isclass(member):
            targets.extend(
                value for value in vars(member).values()
                if inspect.isfunction(value)
            )
        for target in targets:
            try:
                typing.get_type_hints(target)
            except NameError as error:
                unresolved.append(f"{name}: {error}")
            except Exception:
                # 전방 참조가 다른 모듈에 있는 경우 등은 이 테스트의 관심사가 아니다.
                continue

    assert not unresolved, f"{module_name}의 애노테이션이 해석되지 않는다: {unresolved}"
