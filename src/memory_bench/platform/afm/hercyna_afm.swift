// Exploratory Swift 6.4 CLI bridge for macOS 27.2 FoundationModels.framework (`hercyna-afm`).
// Strictly quarantined as an exploratory 1,000-token view-only decider probe (~4k OS context ceiling).
// Never used for full-history gate/floor calls.

import Foundation
#if canImport(FoundationModels)
import FoundationModels
#endif

struct AfmRequest: Decodable {
    let system: String?
    let prompt: String
    let valid_actions: [String]?
}

struct AfmResponse: Encodable {
    let status: String // "ok" | "guardrail_refusal" | "context_exceeded" | "unavailable" | "error"
    let action: String?
    let reason: String?
    let detail: String?
}

func emit(_ resp: AfmResponse) {
    let enc = JSONEncoder()
    if let data = try? enc.encode(resp), let s = String(data: data, encoding: .utf8) {
        print(s)
    }
}

#if canImport(FoundationModels)
@available(macOS 26.0, *)
@Generable
struct ActionDecision {
    @Guide(description: "Single action token chosen strictly from valid_actions.")
    var action: String
    @Guide(description: "Brief one-sentence justification citing decisive record IDs.")
    var reason: String
}
#endif

@main
struct HercynaAfmCLI {
    static func main() async {
        let stdinData = FileHandle.standardInput.readDataToEndOfFile()
        guard let req = try? JSONDecoder().decode(AfmRequest.self, from: stdinData) else {
            emit(AfmResponse(status: "error", action: nil, reason: nil, detail: "invalid_json_stdin"))
            return
        }

        #if canImport(FoundationModels)
        if #available(macOS 26.0, *) {
            let model = SystemLanguageModel.default
            guard model.isAvailable else {
                emit(AfmResponse(status: "unavailable", action: nil, reason: nil, detail: "system_language_model_unavailable"))
                return
            }
            do {
                let instructions = req.system ?? "Return one valid action and brief reason."
                let session = LanguageModelSession(instructions: instructions)
                let reply = try await session.respond(to: req.prompt, generating: ActionDecision.self)
                emit(AfmResponse(status: "ok", action: reply.content.action, reason: reply.content.reason, detail: nil))
            } catch {
                let msg = String(describing: error).lowercased()
                if msg.contains("guardrail") || msg.contains("safety") || msg.contains("refus") {
                    emit(AfmResponse(status: "guardrail_refusal", action: nil, reason: nil, detail: "guardrail"))
                } else if msg.contains("context") || msg.contains("token") || msg.contains("exceed") {
                    emit(AfmResponse(status: "context_exceeded", action: nil, reason: nil, detail: "context_limit"))
                } else {
                    emit(AfmResponse(status: "error", action: nil, reason: nil, detail: "afm_runtime_error"))
                }
            }
            return
        }
        #endif

        emit(AfmResponse(status: "unavailable", action: nil, reason: nil, detail: "foundation_models_unsupported"))
    }
}
