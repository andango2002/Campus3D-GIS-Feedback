# -*- coding: utf-8 -*-
"""Phase 2 legend selection policy (pure Python; no QGIS dependencies).

ordered_layer_ids: temperature/non-temperature QGIS canvas layer IDs in visual
order (topmost first). dataset_layer_ids: one set/list of IDs per dataset.
"""


def select_dataset(dataset_layer_ids, ordered_layer_ids, selected_index, auto_follow):
    """Return (dataset index or -1, visible dataset count, message).

    Never claim the selected dataset is displayed if none of its QGIS map
    layers is visible.  For automatic mode, canvas order decides the winner.
    """
    id_to_dataset = {}
    for index, ids in enumerate(dataset_layer_ids):
        for layer_id in ids:
            id_to_dataset[layer_id] = index
    visible = []
    for layer_id in ordered_layer_ids:
        if layer_id in id_to_dataset:
            index = id_to_dataset[layer_id]
            if index not in visible:
                visible.append(index)
    if auto_follow:
        if not visible:
            return -1, 0, '表示ONのMRT／Surface温度レイヤがありません。'
        index = visible[0]
        detail = ('複数の温度データが表示ONです。最前面のデータを採用。'
                  if len(visible) > 1 else '表示ONの温度レイヤに自動連動中。')
        return index, len(visible), detail
    if not (0 <= selected_index < len(dataset_layer_ids)):
        return -1, len(visible), '手動モードの温度データを選択してください。'
    if selected_index not in visible:
        return -1, len(visible), '手動選択した温度データは非表示です。左側のレイヤをONにしてください。'
    msg = ('複数の温度データが表示ONです。手動選択した凡例を表示中（最前面とは限りません）。'
           if len(visible) > 1 else '手動選択した表示ONの温度レイヤに対応。')
    return selected_index, len(visible), msg
