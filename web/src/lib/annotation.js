/** 解析后端标注 JSON 为统一结构 */

export function flattenAnnotationBoxes(config) {
  if (!config || typeof config !== 'object') return [];

  const shelves = config.shelves;
  if (Array.isArray(shelves) && shelves.length) {
    const out = [];
    for (const shelf of shelves) {
      if (!shelf || !Array.isArray(shelf.boxes)) continue;
      const shelfCode = String(shelf.shelf_code || '').trim();
      for (const box of shelf.boxes) {
        if (!box || !Array.isArray(box.video_polygon) || box.video_polygon.length < 3) continue;
        out.push({
          ...box,
          shelf_code: box.shelf_code || shelfCode,
        });
      }
    }
    if (out.length) return out;
  }

  const boxes = config.boxes;
  if (!Array.isArray(boxes)) return [];
  return boxes.filter((b) => b && Array.isArray(b.video_polygon) && b.video_polygon.length >= 3);
}

function normalizeShelfEntry(shelf) {
  if (!shelf || typeof shelf !== 'object') return null;
  const shelfCode = String(shelf.shelf_code || '').trim();
  if (!shelfCode) return null;
  const shelfBoxes = Array.isArray(shelf.boxes)
    ? shelf.boxes
        .filter((b) => b && Array.isArray(b.video_polygon) && b.video_polygon.length >= 3)
        .map((b) => ({
          ...b,
          shelf_code: String(b.shelf_code || shelfCode).trim() || shelfCode,
        }))
    : [];
  return {
    shelf_code: shelfCode,
    shelf_name: String(shelf.shelf_name || '').trim(),
    shelf_corners: Array.isArray(shelf.shelf_corners) ? shelf.shelf_corners : [],
    grid_shape: Array.isArray(shelf.grid_shape) ? shelf.grid_shape : [],
    boxes: shelfBoxes,
  };
}

export function parseAnnotationShelves(config, payload) {
  const raw = payload?.shelves ?? config?.shelves;
  if (!Array.isArray(raw) || !raw.length) return [];
  return raw.map(normalizeShelfEntry).filter(Boolean);
}

/** 从扁平 boxes 按 shelf_code 拆成多货架（API 未返回 shelves 时兜底） */
export function groupBoxesIntoShelves(boxes) {
  if (!Array.isArray(boxes) || !boxes.length) return [];
  const map = new Map();
  for (const box of boxes) {
    if (!box || !Array.isArray(box.video_polygon) || box.video_polygon.length < 3) continue;
    const shelfCode = String(box.shelf_code || '').trim() || 'DEFAULT';
    if (!map.has(shelfCode)) {
      map.set(shelfCode, {
        shelf_code: shelfCode,
        shelf_name: '',
        shelf_corners: [],
        grid_shape: [],
        boxes: [],
      });
    }
    map.get(shelfCode).boxes.push({
      ...box,
      shelf_code: shelfCode,
    });
  }
  for (const shelf of map.values()) {
    let rows = 0;
    let cols = 0;
    for (const b of shelf.boxes) {
      const layer = Number(b.layer);
      const column = Number(b.column);
      if (layer > 0) rows = Math.max(rows, layer);
      if (column > 0) cols = Math.max(cols, column);
    }
    if (rows > 0 && cols > 0) {
      shelf.grid_shape = [rows, cols];
    }
  }
  return Array.from(map.values());
}

export function resolveMonitorShelves({ shelves = [], boxes = [] }) {
  const normalized = Array.isArray(shelves)
    ? shelves.map(normalizeShelfEntry).filter(Boolean)
    : [];
  if (normalized.length > 1) return normalized;
  const grouped = groupBoxesIntoShelves(boxes);
  if (grouped.length > 1) return grouped;
  if (normalized.length === 1) return normalized;
  if (grouped.length === 1) return grouped;
  return normalized;
}

export function parseAnnotationPayload(payload) {
  if (!payload || payload.error) {
    return { boxes: [], shelves: [], shelfCorners: [], annotationSize: null, gridShape: [] };
  }

  const config = payload.data || payload;
  let shelves = parseAnnotationShelves(config, payload);

  let boxes =
    Array.isArray(payload.boxes) && payload.boxes.length
      ? payload.boxes.map((b) => ({
          ...b,
          shelf_code: String(b.shelf_code || '').trim(),
        }))
      : shelves.length
        ? shelves.flatMap((s) => s.boxes)
        : flattenAnnotationBoxes(config);

  if (!shelves.length && boxes.length) {
    const legacyCode =
      String(config?.source_info?.shelf_code || config?.source_info?.camera_name || '').trim() ||
      'DEFAULT';
    shelves = [
      normalizeShelfEntry({
        shelf_code: legacyCode,
        shelf_corners: config?.shelf_corners,
        grid_shape: config?.grid_shape,
        boxes,
      }) || {
        shelf_code: legacyCode,
        shelf_name: '',
        shelf_corners: [],
        grid_shape: config?.grid_shape || [],
        boxes,
      },
    ];
  }

  let shelfCorners =
    (Array.isArray(payload.shelf_corners) && payload.shelf_corners) ||
    (Array.isArray(config.shelf_corners) && config.shelf_corners) ||
    [];
  if (!shelfCorners.length && shelves.length) {
    const first = shelves.find((s) => s.shelf_corners.length >= 3);
    if (first) shelfCorners = first.shelf_corners;
  }

  shelves = resolveMonitorShelves({ shelves, boxes });

  if (shelves.length) {
    boxes = dedupeAnnotationBoxes(shelves.flatMap((s) => s.boxes || []));
  } else {
    boxes = dedupeAnnotationBoxes(boxes);
  }

  const annotationSize = payload.annotation_size || config.annotation_size || null;
  const gridShape =
    payload.grid_shape ||
    config.grid_shape ||
    (shelves.length === 1 ? shelves[0].grid_shape : []) ||
    [];

  return { boxes, shelves, shelfCorners, annotationSize, gridShape };
}

/** 与后端 box_collision_token 一致：多货架为 ``shelf:box_id``，单货架 legacy 为 ``Box_id`` */
export function boxRoiKey(box) {
  const shelf = String(box?.shelf_code || '').trim();
  const id = box?.box_id ?? box?.id;
  if (id == null || id === '') return '';
  const boxId = String(id);
  if (shelf) return `${shelf}:${boxId}`;
  return `Box_${boxId}`;
}

/** 按 shelf_code + box_id（或 layer/column）去重，避免 API 扁平列表与 shelves 重复 */
export function dedupeAnnotationBoxes(boxes) {
  if (!Array.isArray(boxes)) return [];
  const seen = new Set();
  const out = [];
  for (const box of boxes) {
    if (!box) continue;
    const key =
      boxRoiKey(box) ||
      `${box.shelf_code || ''}:${box.layer || 0}:${box.column || 0}:${box.box_id ?? ''}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(box);
  }
  return out;
}

export function parseCollisionToken(token) {
  const text = String(token || '').trim();
  if (!text) return { shelf_code: '', box_id: '' };
  if (text.startsWith('Box_')) {
    return { shelf_code: '', box_id: text.slice(4) };
  }
  const sep = text.indexOf(':');
  if (sep >= 0) {
    return { shelf_code: text.slice(0, sep), box_id: text.slice(sep + 1) };
  }
  return { shelf_code: '', box_id: text };
}

/** 判断碰撞 token 是否对应某货位（兼容 ``Box_{id}`` 与 ``{shelf}:{id}``） */
export function collisionMatchesBox(token, box) {
  const text = String(token || '').trim();
  if (!text || !box) return false;
  const key = boxRoiKey(box);
  if (key && key === text) return true;

  const parsed = parseCollisionToken(text);
  const boxId = String(box?.box_id ?? box?.id ?? '').trim();
  if (!parsed.box_id || !boxId || parsed.box_id !== boxId) return false;

  // 后端 legacy 扁平 JSON 发 Box_{id}，前端展示可能带 shelf 前缀
  if (text.startsWith('Box_')) return true;

  const boxShelf = String(box?.shelf_code || '').trim();
  if (!parsed.shelf_code || !boxShelf) return true;
  return boxShelf === parsed.shelf_code;
}

export function boxMatchesAnyCollision(box, tokens) {
  if (!box || tokens == null) return false;
  const list = Array.isArray(tokens) ? tokens : [...tokens];
  return list.some((token) => collisionMatchesBox(token, box));
}
