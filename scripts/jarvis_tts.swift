// J.A.R.V.I.S — AVSpeechSynthesizer 기반 자연스러운 TTS (macOS Siri 음성 품질)
//
// macOS `say`는 음성을 "이름"으로만 고를 수 있어, 같은 이름의 compact/enhanced/premium이
// 함께 설치돼 있으면 로봇 같은 compact가 선택되곤 한다. 이 도구는 AVSpeechSynthesizer로
// 음성을 "품질(premium > enhanced > default)"까지 보고 골라, 가장 Siri에 가까운 음성을
// 결정적으로 사용한다.
//
// 사용법:
//   jarvis_tts --list
//   jarvis_tts --out <path.caf> [--voice <이름|식별자>] [--lang ko-KR] [--rate 0.0~1.0]
//              (--text <문장> | --textfile <경로> | stdin)
//
// 종료코드: 0 성공(파일 생성), 그 외 실패. 선택된 음성은 stderr로 로깅.

import AVFoundation
import Foundation

func err(_ s: String) {
    FileHandle.standardError.write((s + "\n").data(using: .utf8)!)
}

func qualityRank(_ v: AVSpeechSynthesisVoice) -> Int {
    switch v.quality {
    case .premium:  return 3
    case .enhanced: return 2
    default:        return 1
    }
}

func qualityName(_ v: AVSpeechSynthesisVoice) -> String {
    switch v.quality {
    case .premium:  return "premium"
    case .enhanced: return "enhanced"
    default:        return "default"
    }
}

// 언어 프리픽스(예: "ko")로 후보 필터
func voices(forLang lang: String) -> [AVSpeechSynthesisVoice] {
    let pfx = String(lang.prefix(2)).lowercased()
    return AVSpeechSynthesisVoice.speechVoices().filter {
        $0.language.lowercased().hasPrefix(pfx)
    }
}

// 선호 이름/식별자 + 품질을 종합해 최적 음성 선택
func pickVoice(pref: String, lang: String) -> AVSpeechSynthesisVoice? {
    let cands = voices(forLang: lang)
    if cands.isEmpty { return nil }

    // 1) 식별자 정확 일치 (예: com.apple.voice.premium.ko-KR.Yuna)
    if pref.contains(".") {
        if let v = cands.first(where: { $0.identifier == pref }) { return v }
    }

    let target = pref.isEmpty ? "Yuna" : pref
    // 2) 이름에 target 을 '포함'하는 후보 중 최고 품질
    //    (프리미엄/향상 음성은 이름이 "Yuna (Premium)" 처럼 접미사가 붙어 정확일치로는 놓친다)
    let named = cands.filter { $0.name.range(of: target, options: .caseInsensitive) != nil }
    let pool = named.isEmpty ? cands : named

    func yunaish(_ v: AVSpeechSynthesisVoice) -> Int {
        return v.name.range(of: "Yuna", options: .caseInsensitive) != nil ? 1 : 0
    }
    // 품질 우선(premium>enhanced>default), 동률이면 Yuna 계열(한국 Siri 음성) 선호
    return pool.max { a, b in
        let qa = qualityRank(a), qb = qualityRank(b)
        if qa != qb { return qa < qb }
        return yunaish(a) < yunaish(b)
    }
}

// ── 인자 파싱 ──
var args = Array(CommandLine.arguments.dropFirst())
func popValue(_ flag: String) -> String? {
    guard let i = args.firstIndex(of: flag), i + 1 < args.count else { return nil }
    let v = args[i + 1]
    args.removeSubrange(i...(i + 1))
    return v
}
let wantList = args.contains("--list")

let lang = popValue("--lang") ?? "ko-KR"

if wantList {
    for v in voices(forLang: lang).sorted(by: { qualityRank($0) > qualityRank($1) }) {
        print("\(qualityName(v))\t\(v.name)\t\(v.language)\t\(v.identifier)")
    }
    exit(0)
}

let outPath = popValue("--out")
let voicePref = popValue("--voice") ?? "Yuna"
let rateStr = popValue("--rate")
var text = popValue("--text")
if let tf = popValue("--textfile") {
    text = (try? String(contentsOfFile: tf, encoding: .utf8)) ?? text
}
if text == nil {
    // stdin 폴백
    let data = FileHandle.standardInput.readDataToEndOfFile()
    text = String(data: data, encoding: .utf8)
}

guard let outPath = outPath, let text = text?.trimmingCharacters(in: .whitespacesAndNewlines), !text.isEmpty else {
    err("[jarvis_tts] usage: --out <path> [--voice N] [--lang ko-KR] [--rate 0..1] --text <..>")
    exit(2)
}

guard let voice = pickVoice(pref: voicePref, lang: lang) else {
    err("[jarvis_tts] no voice for lang=\(lang)")
    exit(3)
}
err("[jarvis_tts] voice=\(voice.name) quality=\(qualityName(voice)) id=\(voice.identifier)")

let utt = AVSpeechUtterance(string: text)
utt.voice = voice
if let rs = rateStr, let r = Float(rs) {
    utt.rate = max(AVSpeechUtteranceMinimumSpeechRate, min(AVSpeechUtteranceMaximumSpeechRate, r))
} else {
    utt.rate = AVSpeechUtteranceDefaultSpeechRate   // 가장 자연스러운 기본 속도
}
utt.pitchMultiplier = 1.0
utt.postUtteranceDelay = 0.0

let url = URL(fileURLWithPath: outPath)
try? FileManager.default.removeItem(at: url)

// AVSpeechSynthesizer.write 는 버퍼를 GCD 큐로 비동기 전달한다. CLI에서는 RunLoop 대신
// dispatchMain()으로 큐를 돌리고, 완료/오류/타임아웃 시 콜백/타이머에서 exit()으로 마무리한다.
final class Sink { var file: AVAudioFile? }
let sink = Sink()
let synth = AVSpeechSynthesizer()

func finishOK() {
    sink.file = nil   // flush/close
    let reachable = (try? url.checkResourceIsReachable()) ?? false
    let size = ((try? FileManager.default.attributesOfItem(atPath: outPath)[.size]) as? Int) ?? 0
    if !reachable || size <= 0 {
        err("[jarvis_tts] empty output")
        exit(5)
    }
    exit(0)
}
func finishFail(_ code: Int32, _ why: String) {
    sink.file = nil
    err("[jarvis_tts] \(why)")
    exit(code)
}

// 안전장치: 어떤 이유로든 버퍼가 오지 않으면 60초 후 실패 종료(→ 파이썬이 say로 폴백).
DispatchQueue.main.asyncAfter(deadline: .now() + 60) {
    finishFail(6, "timeout (no audio buffers)")
}

synth.write(utt) { (buf: AVAudioBuffer) in
    guard let pcm = buf as? AVAudioPCMBuffer else { return }
    if pcm.frameLength == 0 {
        DispatchQueue.main.async { finishOK() }
        return
    }
    do {
        if sink.file == nil {
            sink.file = try AVAudioFile(forWriting: url, settings: pcm.format.settings)
        }
        try sink.file?.write(from: pcm)
    } catch {
        DispatchQueue.main.async { finishFail(4, "write error: \(error)") }
    }
}

dispatchMain()
