"""Decide whether recognized microphone speech is addressed to Alfred."""

from alfred.action_catalog import category_criteria


class VoiceAddressGate:
    def __init__(self, decider, conversation_memory, capability_provider=category_criteria):
        self.decider = decider
        self.conversation_memory = conversation_memory
        self.capability_provider = capability_provider

    def accepts(self, text: str, *, followup_active: bool, during_tts: bool, recent_tts: bool = False) -> bool:
        history = [
            {"role": entry.get("role", ""), "content": entry.get("content", "")[:240]}
            for entry in (
                self.conversation_memory.recent_history(8)
                if followup_active or during_tts or recent_tts else []
            )
        ]
        source_criteria = {
            "other_person": "Speech directed to another person, including a named human addressee.",
            "self_talk": "Speaker expresses a thought or plan aloud without asking for a response.",
            "media": "Narration, commentary or instruction to an external audience, not a live request.",
            "unclear": "The intended addressee is not evident from the speech alone.",
            "assistant": "The speaker expects Alfred to reply or act.",
        }
        address_criteria = {
            "other_person": "The speaker addresses another person, who is expected to respond or act; this includes an unaddressed request for bodily movement or handling physical objects that Alfred cannot perform.",
            "self_talk": "The speaker states a thought or plan aloud without asking anyone to respond or act.",
            "media": "The speech narrates a process, quotes dialogue, or comments to an external audience rather than requesting Alfred to act or answer.",
            "unclear": "The addressee or source is not clear enough to conclude the speech is directed to Alfred.",
            "assistant": "The speaker explicitly addresses Alfred, asks him a question or for advice or about his own abilities, requests an available computer action, or naturally continues a conversation with him. Explicitly naming Alfred or asking whether he can do something addresses him even if the action is impossible.",
        }
        recent_reply = next(
            (entry.get("content", "") for entry in reversed(history)
             if entry.get("role") == "assistant"),
            "",
        )
        try:
            if (during_tts or recent_tts) and recent_reply:
                echo = self.decider.choose(
                    {
                        "recognized_speech": text,
                        "recent_assistant_reply": recent_reply,
                    },
                    "Does recognized_speech repeat content from recent_assistant_reply, "
                    "likely captured through microphone playback? A short human response "
                    "to the assistant is independent speech even if it occurs during playback.",
                    {
                        "echo": "The speech repeats or closely paraphrases a span of the recent assistant reply.",
                        "new_speech": "The speech is distinct from the recent assistant reply.",
                    },
                )
                if echo == "echo":
                    print(f"[VOICE] Assistant audio echo ignored: {text}")
                    return False
            source = self.decider.choose(
                {
                    "assistant_name": "Alfred",
                    "recognized_speech": text,
                    "recent_conversation": history,
                    "conversation_continuation_window": followup_active,
                    "speech_started_during_assistant_audio": during_tts,
                    "speech_started_just_after_assistant_audio": recent_tts,
                    "assistant_audio_echo_risk": during_tts or recent_tts,
                },
                "Identify the intended addressee or source of this speech before considering "
                "what Alfred is able to do. A command or question alone does not prove it is "
                "for Alfred. Another named addressee takes precedence. A spoken plan or "
                "narration is not a request. Use supplied recent conversation as context.",
                source_criteria,
            )
            if source in {"other_person", "self_talk", "media"}:
                print(f"[VOICE] Decider source={source}; speech ignored: {text}")
                return False
            decision = self.decider.choose(
                {
                    "assistant_name": "Alfred",
                    "recognized_speech": text,
                    "recent_conversation": history,
                    "recent_assistant_reply": recent_reply,
                    "assistant_capabilities": [
                        "Converse and answer questions",
                        *self.capability_provider().values(),
                    ],
                    "assistant_limitations": "Alfred is software without a body or robot. He cannot move through rooms, pick up, carry, or physically manipulate objects.",
                    "conversation_continuation_window": followup_active,
                    "speech_started_during_assistant_audio": during_tts,
                    "speech_started_just_after_assistant_audio": recent_tts,
                    "assistant_audio_echo_risk": during_tts or recent_tts,
                },
                "Given that the first pass did not establish an external addressee or narration, "
                "who is expected to act or respond to this speech now? If Alfred is explicitly named, "
                "choose assistant even when the action is impossible for him. A question asking "
                "whether Alfred can do something is addressed to Alfred, even if he cannot do it. "
                "Questions asking for information or advice can address Alfred regardless of "
                "whether he can perform the task. "
                "Interpret the whole utterance "
                "and recent conversation. A grammatical command or question alone does not prove "
                "the addressee is Alfred. Another named addressee takes precedence over a generic "
                "computer command. "
                "A natural follow-up to Alfred's latest answer can address him without naming him. "
                "A statement narrating a process to an audience or describing one's own plan "
                "does not become a request merely because Alfred could perform a similar action. "
                "Capabilities and limitations help judge the likely addressee. An unaddressed "
                "request for bodily movement or physical handling is likely for a person nearby. "
                "If the speaker explicitly addresses Alfred, he should still respond truthfully "
                "even when he cannot carry out the request. Do not restrict the wording of "
                "requests Alfred can perform. "
                "The continuation window is context, not automatic permission. "
                "Choose unclear when the intended addressee cannot be determined.",
                address_criteria,
            )
            if source == "assistant" and decision == "other_person":
                speech_act = self.decider.choose(
                    {
                        "recognized_speech": text,
                        "assistant_name": "Alfred",
                        "assistant_has_body": False,
                    },
                    "Is the speaker asking Alfred for information, advice or an explanation, "
                    "or directing someone nearby to perform bodily work? Distinguish an "
                    "information question from an imperative request.",
                    {
                        "information_question": "A question requesting an answer, explanation, instructions or clarification about Alfred's abilities.",
                        "physical_request": "An imperative to physically move or handle an object without addressing Alfred by name.",
                        "other": "Neither of these speech acts applies.",
                    },
                )
                if speech_act == "information_question":
                    decision = "assistant"
            if decision == "assistant":
                outcome = self.decider.choose(
                    {
                        "utterance": text,
                        "recent_conversation": history,
                        "available_abilities": ["conversation", *self.capability_provider().keys()],
                        "unavailable_abilities": "No smart-home connection, robot, or body.",
                    },
                    "Classify the outcome requested by this utterance. A question asking HOW "
                    "to do something is conversation. A command to change the physical "
                    "environment without a connected actuator is environment, not computer "
                    "control. A standalone fragment with no clear goal is ambiguous.",
                    {
                        "environment": "The requested outcome changes the physical environment using an unavailable actuator or smart-home connection.",
                        "computer": "The requested outcome can be produced by Alfred's computer software or computer controls.",
                        "conversation": "The utterance asks for information, advice or ordinary conversation, including a question about an unavailable action.",
                        "ambiguous": "The utterance has no clear request or goal even when considering recent conversation.",
                    },
                )
                if outcome == "ambiguous":
                    decision = "unclear"
                elif outcome == "environment":
                    speech_act = self.decider.choose(
                        {"utterance": text},
                        "Classify the speech act, not the topic. Does the speaker ask HOW "
                        "to do something or COMMAND someone to do it now?",
                        {
                            "question": "The speaker requests an explanation, instructions, or asks about an ability.",
                            "command": "The speaker orders an action to happen now.",
                        },
                    )
                    if speech_act == "command":
                        named = self.decider.choose(
                            {"utterance": text, "assistant_name": "Alfred"},
                            "Is the assistant explicitly addressed by name in the utterance? "
                            "Determine only whether the name is spoken, not whether the request is possible.",
                            {
                                "named": "The utterance contains the name Alfred as a direct form of address to him.",
                                "not_named": "The utterance does not explicitly name Alfred as an addressee.",
                            },
                        )
                        if named == "not_named":
                            decision = "other_person"
        except Exception as exc:
            print(f"[VOICE] Addressing decision failed; speech ignored: {exc}")
            return False
        accepted = decision == "assistant"
        print(f"[VOICE] Decider addressing={decision} for: {text}")
        return accepted
