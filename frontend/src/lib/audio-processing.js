import { DEFAULT_RECORDING_CONFIG, clampRecordingConfig } from "@/lib/recording-config";

export const LIVE_WAVEFORM_BAR_COUNT = 256;

export function concatFloat32(chunks) {
  const length = chunks.reduce((total, chunk) => total + chunk.length, 0);
  const output = new Float32Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    output.set(chunk, offset);
    offset += chunk.length;
  }
  return output;
}

function writeAscii(view, offset, value) {
  for (let index = 0; index < value.length; index += 1) {
    view.setUint8(offset + index, value.charCodeAt(index));
  }
}

export function encodeWav(samples, sampleRate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  writeAscii(view, 0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeAscii(view, 8, "WAVE");
  writeAscii(view, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeAscii(view, 36, "data");
  view.setUint32(40, samples.length * 2, true);
  let offset = 44;
  for (let index = 0; index < samples.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, samples[index]));
    view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
    offset += 2;
  }
  return new Blob([view], { type: "audio/wav" });
}

export function applyInputGain(input, gain) {
  const normalizedGain = Number.isFinite(gain) ? Math.min(2.5, Math.max(0.8, gain)) : 1;
  const frame = new Float32Array(input.length);
  if (Math.abs(normalizedGain - 1) < 0.001) {
    frame.set(input);
    return frame;
  }
  for (let index = 0; index < input.length; index += 1) {
    frame[index] = Math.max(-1, Math.min(1, input[index] * normalizedGain));
  }
  return frame;
}

function smoothLiveBars(values) {
  if (!values.length) return [];
  return values.map((value, index) => {
    const prev = values[index - 1] ?? value;
    const next = values[index + 1] ?? value;
    return Math.min(0.84, Math.max(0.018, prev * 0.18 + value * 0.64 + next * 0.18));
  });
}

export function compressWaveformBars(values, targetCount = LIVE_WAVEFORM_BAR_COUNT) {
  if (values.length <= targetCount) return smoothLiveBars(values);
  const bars = Array.from({ length: targetCount }, (_, index) => {
    const start = Math.floor(index * values.length / targetCount);
    const end = Math.max(start + 1, Math.floor((index + 1) * values.length / targetCount));
    let peak = 0;
    let total = 0;
    let count = 0;
    for (let item = start; item < end; item += 1) {
      const value = values[item] || 0;
      peak = Math.max(peak, value);
      total += value;
      count += 1;
    }
    const mean = count ? total / count : 0;
    return Math.min(0.84, mean * 0.48 + peak * 0.52);
  });
  return smoothLiveBars(bars);
}

export function shapeLiveWaveLevel(rms, peak, ceilingRef, floorRef) {
  const energy = Math.max(0, Number(rms) || 0) * 0.78 + Math.max(0, Number(peak) || 0) * 0.22;
  const previousCeiling = Math.max(0.018, Number(ceilingRef.current) || 0.055);
  const previousFloor = Math.max(0.0006, Number(floorRef.current) || 0.002);
  const floorTarget = energy < previousCeiling * 0.35 ? energy : previousFloor;
  const nextFloor = Math.min(0.025, Math.max(0.0006, previousFloor + (floorTarget - previousFloor) * 0.008));
  const ceilingTarget = Math.max(energy, nextFloor + 0.018, 0.035);
  const ceilingSpeed = ceilingTarget > previousCeiling ? 0.22 : 0.012;
  const nextCeiling = previousCeiling + (ceilingTarget - previousCeiling) * ceilingSpeed;
  floorRef.current = nextFloor;
  ceilingRef.current = nextCeiling;

  const normalized = Math.max(0, Math.min(1, (energy - nextFloor * 0.85) / Math.max(0.012, nextCeiling - nextFloor * 0.85)));
  if (normalized <= 0.01) return 0.018;
  const compressed = Math.log1p(normalized * 5) / Math.log1p(5);
  return Math.min(0.84, Math.max(0.026, Math.pow(compressed, 0.86) * 0.78 + 0.026));
}

export function displayMicLevel(level) {
  const shaped = Math.pow(Math.max(0, Number(level) || 0) * 10, 0.66) * 0.72 + 0.04;
  return Math.min(0.92, Math.max(0.08, shaped));
}

export function audioBufferToMono(audioBuffer) {
  const length = audioBuffer.length;
  const channelCount = audioBuffer.numberOfChannels;
  const output = new Float32Array(length);
  for (let channel = 0; channel < channelCount; channel += 1) {
    const data = audioBuffer.getChannelData(channel);
    for (let index = 0; index < length; index += 1) {
      output[index] += data[index] / channelCount;
    }
  }
  return output;
}

export function makeFixedChunks(samples, sampleRate, config = DEFAULT_RECORDING_CONFIG) {
  const normalized = clampRecordingConfig(config);
  const maxSamples = Math.round(sampleRate * normalized.maxSegmentMs / 1000);
  const chunks = [];
  for (let start = 0; start < samples.length; start += maxSamples) {
    const end = Math.min(samples.length, start + maxSamples);
    chunks.push({
      samples: samples.slice(start, end),
      startedAtMs: start * 1000 / sampleRate,
      endedAtMs: end * 1000 / sampleRate,
      cutReason: "导入固定切片",
    });
  }
  return chunks;
}
