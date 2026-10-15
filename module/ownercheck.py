import logging
import json


def get_owner_ids() -> frozenset[int]:
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)

        configured_ids = config.get('OWNER_IDS')
        if configured_ids is None:
            configured_ids = [config.get('OWNER_ID')]
        elif not isinstance(configured_ids, list):
            raise TypeError('OWNER_IDS 必須是清單')

        owner_ids = set()
        for owner_id in configured_ids:
            if isinstance(owner_id, bool):
                raise ValueError('Discord 使用者 ID 不可為布林值')
            if owner_id is not None:
                owner_ids.add(int(owner_id))
        return frozenset(owner_ids)
    except Exception as e:
        logging.warning(f"⚠️ 讀取 OWNER_IDS 時發生錯誤: {e}")
        return frozenset()


OWNER_IDS = get_owner_ids()


def is_owner(user_id: int) -> bool:
    return user_id in OWNER_IDS
