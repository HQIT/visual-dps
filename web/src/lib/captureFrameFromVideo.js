import Hls from 'hls.js';

/** 从 <video>（HLS/WebRTC）截取 JPEG base64（不含 data: 前缀） */
export function captureVideoFrameToBase64(videoEl, quality = 0.85) {
  if (!videoEl) {
    throw new Error('视频未就绪');
  }
  const w = videoEl.videoWidth;
  const h = videoEl.videoHeight;
  if (!w || !h) {
    throw new Error('视频尚未出画，请稍候再抓帧');
  }
  const canvas = document.createElement('canvas');
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext('2d');
  if (!ctx) {
    throw new Error('无法创建画布');
  }
  ctx.drawImage(videoEl, 0, 0, w, h);
  const dataUrl = canvas.toDataURL('image/jpeg', quality);
  const comma = dataUrl.indexOf(',');
  return comma >= 0 ? dataUrl.slice(comma + 1) : dataUrl;
}

/** 总览页快捷抓帧：隐藏 video + HLS，不跳转监控页 */
export function captureThumbnailFromHls(hlsUrl, timeoutMs = 15000) {
  return new Promise((resolve, reject) => {
    const video = document.createElement('video');
    video.muted = true;
    video.playsInline = true;
    video.setAttribute('playsinline', '');
    video.style.cssText = 'position:fixed;left:-9999px;width:1px;height:1px;opacity:0;pointer-events:none';
    document.body.appendChild(video);

    let hls = null;
    let finished = false;

    const cleanup = () => {
      if (hls) {
        hls.destroy();
        hls = null;
      }
      video.pause();
      video.removeAttribute('src');
      video.srcObject = null;
      video.remove();
    };

    const finish = (fn, value) => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      cleanup();
      fn(value);
    };

    const timer = setTimeout(
      () => finish(reject, new Error('抓帧超时，请确认该路已有画面')),
      timeoutMs,
    );

    const onFrame = () => {
      try {
        finish(resolve, captureVideoFrameToBase64(video));
      } catch (err) {
        finish(reject, err);
      }
    };

    video.addEventListener('loadeddata', onFrame, { once: true });

    if (Hls.isSupported()) {
      hls = new Hls({
        enableWorker: true,
        xhrSetup: (xhr) => {
          xhr.withCredentials = true;
        },
      });
      hls.on(Hls.Events.ERROR, (_, data) => {
        if (data.fatal) {
          finish(reject, new Error(`HLS 加载失败: ${data.details || data.type}`));
        }
      });
      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        video.play().catch(() => {});
      });
      hls.loadSource(hlsUrl);
      hls.attachMedia(video);
    } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = hlsUrl;
      video.addEventListener('loadedmetadata', () => {
        video.play().catch(() => {});
      }, { once: true });
    } else {
      finish(reject, new Error('当前浏览器不支持 HLS'));
    }
  });
}
