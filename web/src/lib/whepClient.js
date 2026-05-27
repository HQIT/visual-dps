/**
 * MediaMTX WHEP 播放（对齐 mediamtx v1.11 reader.js：Trickle ICE + SDP editOffer）
 */

function enableStereoOpus(section) {
  let opusPayloadFormat = '';
  const lines = section.split('\r\n');
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].startsWith('a=rtpmap:') && lines[i].toLowerCase().includes('opus/')) {
      opusPayloadFormat = lines[i].slice('a=rtpmap:'.length).split(' ')[0];
      break;
    }
  }
  if (!opusPayloadFormat) return section;
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].startsWith(`a=fmtp:${opusPayloadFormat} `)) {
      if (!lines[i].includes('stereo')) lines[i] += ';stereo=1';
      if (!lines[i].includes('sprop-stereo')) lines[i] += ';sprop-stereo=1';
    }
  }
  return lines.join('\r\n');
}

function editOffer(sdp) {
  const sections = sdp.split('m=');
  for (let i = 0; i < sections.length; i++) {
    if (sections[i].startsWith('audio')) {
      sections[i] = enableStereoOpus(sections[i]);
      break;
    }
  }
  return sections.join('m=');
}

function parseOffer(sdp) {
  const ret = { iceUfrag: '', icePwd: '', medias: [] };
  for (const line of sdp.split('\r\n')) {
    if (line.startsWith('m=')) ret.medias.push(line.slice(2));
    else if (!ret.iceUfrag && line.startsWith('a=ice-ufrag:')) ret.iceUfrag = line.slice(12);
    else if (!ret.icePwd && line.startsWith('a=ice-pwd:')) ret.icePwd = line.slice(10);
  }
  return ret;
}

function generateSdpFragment(od, candidates) {
  const candidatesByMedia = {};
  for (const candidate of candidates) {
    const mid = candidate.sdpMLineIndex;
    if (candidatesByMedia[mid] === undefined) candidatesByMedia[mid] = [];
    candidatesByMedia[mid].push(candidate);
  }
  let frag = `a=ice-ufrag:${od.iceUfrag}\r\na=ice-pwd:${od.icePwd}\r\n`;
  let mid = 0;
  for (const media of od.medias) {
    if (candidatesByMedia[mid] !== undefined) {
      frag += `m=${media}\r\na=mid:${mid}\r\n`;
      for (const candidate of candidatesByMedia[mid]) {
        frag += `a=${candidate.candidate}\r\n`;
      }
    }
    mid += 1;
  }
  return frag;
}

function linkToIceServers(links) {
  if (!links) return [];
  return links.split(', ').map((link) => {
    const m = link.match(
      /^<(.+?)>; rel="ice-server"(; username="(.*?)"; credential="(.*?)"; credential-type="password")?/i,
    );
    if (!m) return null;
    const ret = { urls: [m[1]] };
    if (m[3] !== undefined) {
      ret.username = JSON.parse(`"${m[3]}"`);
      ret.credential = JSON.parse(`"${m[4]}"`);
      ret.credentialType = 'password';
    }
    return ret;
  }).filter(Boolean);
}

async function requestIceServers(whepUrl) {
  try {
    const res = await fetch(whepUrl, { method: 'OPTIONS', credentials: 'include' });
    return linkToIceServers(res.headers.get('Link'));
  } catch {
    return [];
  }
}

/**
 * @returns {Promise<{ pc: RTCPeerConnection, sessionUrl: string, stop: () => void }>}
 */
export async function startWhep(whepUrl, videoEl, onIceFailed) {
  const iceServers = await requestIceServers(whepUrl);
  const pc = new RTCPeerConnection({
    iceServers: iceServers.length ? iceServers : [{ urls: 'stun:stun.l.google.com:19302' }],
  });
  pc.addTransceiver('video', { direction: 'recvonly' });
  pc.addTransceiver('audio', { direction: 'recvonly' });

  let sessionUrl = null;
  let offerData = null;
  const queuedCandidates = [];
  let stopped = false;

  const sendLocalCandidates = async (candidates) => {
    if (!sessionUrl || stopped) return;
    const res = await fetch(sessionUrl, {
      method: 'PATCH',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/trickle-ice-sdpfrag',
        'If-Match': '*',
      },
      body: generateSdpFragment(offerData, candidates),
    });
    if (res.status === 404) throw new Error('WHEP 会话不存在');
    if (res.status !== 204) throw new Error(`Trickle ICE 失败 (HTTP ${res.status})`);
  };

  pc.onicecandidate = (evt) => {
    if (stopped || !evt.candidate) return;
    if (!sessionUrl) queuedCandidates.push(evt.candidate);
    else sendLocalCandidates([evt.candidate]).catch((err) => onIceFailed?.(err.message));
  };

  pc.ontrack = (ev) => {
    const [stream] = ev.streams;
    if (stream) {
      videoEl.srcObject = stream;
      videoEl.play().catch(() => {});
    }
  };

  pc.oniceconnectionstatechange = () => {
    if (stopped) return;
    const st = pc.iceConnectionState;
    if (st === 'failed' || st === 'closed') {
      onIceFailed?.(
        'WebRTC 媒体连接失败：请确认 .env 中 MEDIAMTX_PUBLIC_HOST 为浏览器可访问的 IP，且已映射 UDP/TCP 8189（MEDIAMTX_WEBRTC_ICE_PORT）',
      );
    }
  };

  let offer = await pc.createOffer();
  offer = { type: offer.type, sdp: editOffer(offer.sdp) };
  offerData = parseOffer(offer.sdp);
  await pc.setLocalDescription(offer);

  const resp = await fetch(whepUrl, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/sdp' },
    body: offer.sdp,
  });

  if (!resp.ok) {
    let detail = '';
    try {
      const ct = resp.headers.get('content-type') || '';
      if (ct.includes('json')) {
        const j = await resp.json();
        detail = j.hint || j.error || '';
      } else {
        detail = (await resp.text()).slice(0, 200);
      }
    } catch {
      /* ignore */
    }
    const raw = detail || `WebRTC 信令失败 (HTTP ${resp.status})，请确认 MediaMTX 已开启 WebRTC（8889）且该路已有画面`;
    if (/codec|not supported by client|H265|HEVC/i.test(raw)) {
      throw new Error(
        'WebRTC：浏览器不支持当前视频编码（多为 H.265）。请将海康码流改为 H.264 后重试，或在监控页切换到 HLS。',
      );
    }
    throw new Error(raw);
  }

  const loc = resp.headers.get('location');
  if (!loc) throw new Error('WHEP 未返回 Location，请升级 UI 并重试');
  sessionUrl = new URL(loc, window.location.origin).toString();

  const answerSdp = await resp.text();
  if (!answerSdp.trim()) {
    throw new Error('WebRTC 未返回 SDP，请确认该路径在 MediaMTX 上已有画面');
  }
  await pc.setRemoteDescription({ type: 'answer', sdp: answerSdp });

  if (queuedCandidates.length) {
    await sendLocalCandidates(queuedCandidates);
  }

  const stop = () => {
    if (stopped) return;
    stopped = true;
    const url = sessionUrl;
    sessionUrl = null;
    pc.close();
    if (videoEl) videoEl.srcObject = null;
    if (url) {
      fetch(url, { method: 'DELETE', credentials: 'include' }).catch(() => {});
    }
  };

  return { pc, sessionUrl, stop };
}

export function stopWhep(session) {
  session?.stop?.();
}
