# VoiceMeeting

VoiceMeeting turns long conversations into searchable transcripts and polished meeting notes, while keeping the speech recognition work on your own Mac. Record a meeting, import an audio file, review the timeline, clean up the transcript, and generate Markdown notes without sending raw audio to a cloud ASR service.

> Version `0.0.1` is a macOS-first release. Windows and Linux are not packaged yet.

## Why VoiceMeeting

- **Private by design**: speech-to-text runs locally with Whisper-family models, including Apple Silicon MLX models on Mac.
- **Built for real meetings**: timeline playback, speaker-aware transcript review, editable copies, re-transcription, proofreading, and paragraph cleanup are part of the same workflow.
- **No mystery pipeline**: downloaded models, transcript versions, notes, and audio chunks are visible and managed locally.
- **Bring your own assistant**: meeting notes can use VibeAround or an OpenAI Chat Completions-compatible endpoint.
- **Bilingual interface**: switch between Chinese and English from the top bar.

## Current Features

- Record from the microphone or import an existing audio/video file.
- Use local faster-whisper or Mac MLX Whisper models.
- Preload the selected ASR model before recording so the first transcription does not appear stuck.
- Generate transcript versions for re-recognition, speaker recalibration, text proofreading, paragraph organization, and manual editing.
- Play back audio on a visual timeline and jump directly from transcript segments.
- Generate, stream, render, and download Markdown meeting notes.
- Save transcripts and notes through native macOS save dialogs in the desktop app.
- Manage local model downloads with progress and install/delete controls.

## Download

The first public build is macOS only:

- Apple Silicon: `VoiceMeeting_0.0.1_aarch64.dmg`

The app is signed and notarized for macOS distribution.

## Development

```bash
./scripts/setup.sh
./scripts/dev.sh
```

Open `http://127.0.0.1:5199`.

## Desktop Build

```bash
bun run build:desktop
./scripts/sign-and-notarize-macos.sh
```

The notarized DMG is written to:

```text
tauri/src-tauri/target/release/bundle/dmg/
```

Signing credentials live in `apple-sign.config`, which is intentionally ignored by git.

## Assistant Configuration

VoiceMeeting supports two note-generation routes:

- **VibeAround**: local channel for the meeting assistant.
- **API endpoint**: OpenAI Chat Completions-compatible APIs only. Configure a BaseURL that serves `/v1/chat/completions`, an API key, and a model name.

## 中文说明

VoiceMeeting 是一个面向会议录音和音频转写的本地桌面应用：录音、导入音频、查看时间线、整理逐字稿、生成 Markdown 会议纪要，都在一个界面里完成。语音识别默认在本机运行，不需要把原始音频交给云端 ASR。

> `0.0.1` 是 macOS 首发版本，暂时只提供 Mac 安装包。

### 亮点

- **隐私优先**：语音识别在本机完成，Mac 上可使用 Apple Silicon 友好的 MLX Whisper 模型。
- **适合真实会议**：音频时间线、逐字稿回放跳转、说话人校准、自动校对、段落整理、可编辑副本都在同一套流程里。
- **流程透明**：模型文件、转写版本、音频片段和纪要都保存在本地，可在设置里管理。
- **可接入自己的助手**：纪要生成支持 VibeAround，也支持 OpenAI Chat Completions 兼容接口。
- **中英文界面**：顶栏可一键切换中文和英文。

### 本地开发

```bash
./scripts/setup.sh
./scripts/dev.sh
```

然后打开 `http://127.0.0.1:5199`。

### 打包发布

```bash
bun run build:desktop
./scripts/sign-and-notarize-macos.sh
```

生成的 DMG 位于：

```text
tauri/src-tauri/target/release/bundle/dmg/
```

### 会议助手接口

如果不用 VibeAround，可以在设置里配置“接口”。当前只支持 OpenAI Chat Completions 兼容接口，也就是需要能访问 `/v1/chat/completions` 的 BaseURL。
