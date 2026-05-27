import { useEffect, useRef, useState } from 'react';
import Hls from 'hls.js';
import { startWhep, stopWhep } from '../lib/whepClient';

function formatCodecPlaybackError(message) {
  const raw = String(message || '');
  if (!/codec|not supported by client|H265|HEVC|hev1|hvc1/i.test(raw)) {
    return raw;
  }
  return (
    '当前浏览器无法解码该路视频编码（多为 H.265/HEVC）。' +
    '请在摄像机 Web 将码流改为 H.264，或 pull_url 使用已改为 H.264 的通道（如 Channels/101），' +
    '同步 MediaMTX 后优先用 HLS 播放；子码流仍为 HEVC 时 WebRTC/HLS 均可能失败。'
  );
}

/** 按格式驱动 video 预览源（HLS / WebRTC，经 MediaMTX） */
export function usePreviewStream({ format, playback, videoRef, enabled = true }) {
  const hlsRef = useRef(null);
  const whepRef = useRef(null);
  const [streamError, setStreamError] = useState('');

  useEffect(() => {
    if (!enabled) return undefined;
    const video = videoRef?.current;
    setStreamError('');

    const cleanup = () => {
      if (hlsRef.current) {
        hlsRef.current.destroy();
        hlsRef.current = null;
      }
      stopWhep(whepRef.current);
      whepRef.current = null;
      if (video) {
        video.removeAttribute('src');
        video.srcObject = null;
      }
    };

    if (!video) return cleanup;

    if (format === 'hls') {
      const hlsUrl = playback?.formats?.hls?.url;
      if (!hlsUrl) {
        setStreamError('HLS 不可用（需 MediaMTX 托管摄像头）');
        return cleanup;
      }
      cleanup();
      if (Hls.isSupported()) {
        const hls = new Hls({
          enableWorker: true,
          lowLatencyMode: false,
          xhrSetup: (xhr) => {
            xhr.withCredentials = true;
          },
        });
        hlsRef.current = hls;
        hls.on(Hls.Events.MANIFEST_PARSED, () => {
          video.play().catch(() => {});
        });
        hls.on(Hls.Events.ERROR, (_, data) => {
          if (!data.fatal) return;
          const detail = data.details || data.type || '';
          setStreamError(
            /bufferAppend|manifest|codec|H265|HEVC/i.test(String(detail))
              ? formatCodecPlaybackError(detail)
              : `HLS 播放失败: ${detail}`,
          );
        });
        hls.loadSource(hlsUrl);
        hls.attachMedia(video);
      } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
        video.src = hlsUrl;
        video.play().catch(() => {});
      } else {
        setStreamError('当前浏览器不支持 HLS');
      }
      return cleanup;
    }

    if (format === 'webrtc') {
      const whepUrl = playback?.formats?.webrtc?.url;
      if (!whepUrl) {
        setStreamError('WebRTC 不可用（需 MediaMTX 托管摄像头）');
        return cleanup;
      }
      cleanup();
      let cancelled = false;
      (async () => {
        try {
          const session = await startWhep(whepUrl, video, (msg) => {
            if (!cancelled) setStreamError(formatCodecPlaybackError(msg));
          });
          if (cancelled) {
            stopWhep(session);
            return;
          }
          whepRef.current = session;
        } catch (err) {
          if (!cancelled) {
            const msg = err?.message || 'WebRTC 连接失败';
            if (err?.name === 'TypeError' && /fetch|network/i.test(msg)) {
              setStreamError('无法连接 WebRTC 信令，请确认 MediaMTX 已启动（8889）');
            } else {
              setStreamError(formatCodecPlaybackError(msg));
            }
          }
        }
      })();
      return () => {
        cancelled = true;
        cleanup();
      };
    }

    return cleanup;
  }, [enabled, format, playback, videoRef]);

  return { streamError };
}
