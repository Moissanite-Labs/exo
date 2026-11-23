---- MODULE TraceSeatBinding ----
(* Replay a single JSONL trace: each event is one SeatBinding step; post-states must equal the action result. *)

EXTENDS SeatBinding, TraceInstance

VARIABLES step

traceVars ==
  <<step, coreSeat, liveSessions, foreignSessions, sessionSeat, clientLaunch,
     clientDisplay, clientSession, resumedOtherSeat, lastOutcome, fromClientNew,
     refusedThisNew, nextSid>>

TraceRefused(i) ==
  (PostLastOutcome[i] = "VisibleRefusal")
  \/ (PostLastOutcome[i] = "VisibleJoin")
  \/ (PostLastOutcome[i] = "SilentFail" /\ PostFromClientNew[i])

MatchState(i) ==
  /\ coreSeat = PostCoreSeat[i]
  /\ clientLaunch = PostClientLaunch[i]
  /\ clientDisplay = PostClientDisplay[i]
  /\ clientSession = PostClientSession[i]
  /\ resumedOtherSeat = PostResumedOther[i]
  /\ lastOutcome = PostLastOutcome[i]
  /\ fromClientNew = PostFromClientNew[i]
  /\ refusedThisNew = TraceRefused(i)
  /\ nextSid = PostNextSid[i]
  /\ liveSessions = PostLiveSet(i)
  /\ foreignSessions = PostForeignSet(i)
  /\ sessionSeat = PostSessionSeatFn(i)

PostCondition(j) ==
  /\ coreSeat' = PostCoreSeat[j + 1]
  /\ clientLaunch' = PostClientLaunch[j + 1]
  /\ clientDisplay' = PostClientDisplay[j + 1]
  /\ clientSession' = PostClientSession[j + 1]
  /\ resumedOtherSeat' = PostResumedOther[j + 1]
  /\ lastOutcome' = PostLastOutcome[j + 1]
  /\ fromClientNew' = PostFromClientNew[j + 1]
  /\ refusedThisNew' = TraceRefused(j + 1)
  /\ nextSid' = PostNextSid[j + 1]
  /\ liveSessions' = PostLiveSet(j + 1)
  /\ foreignSessions' = PostForeignSet(j + 1)
  /\ sessionSeat' = PostSessionSeatFn(j + 1)

TraceAction(j) ==
  CASE EventKind[j] = "load_seat" -> LoadCoreSeat(EventSeat[j])
    [] EventKind[j] = "client_boot" -> ClientBoot(EventLaunch[j])
    [] EventKind[j] = "persist_session" -> PersistSession(EventSeat[j])
    [] EventKind[j] = "session_create" -> SessionCreate(EventReqSeat[j])
    [] EventKind[j] = "refusal" -> SessionCreate(EventReqSeat[j])
    [] EventKind[j] = "resume" -> ResumeSession(EventSession[j])
    [] EventKind[j] = "client_new" -> ClientNew
    [] EventKind[j] = "reconnect" -> Reconnect
    [] OTHER -> FALSE

TraceDo(j) ==
  /\ TraceAction(j)
  /\ PostCondition(j)

TraceInit ==
  /\ step = 0
  /\ MatchState(1)

TraceNext ==
  \/ /\ step < TraceLen
     /\ MatchState(step + 1)
     /\ TraceDo(step + 1)
     /\ step' = step + 1
  \/ /\ step = TraceLen
     /\ UNCHANGED traceVars

TraceStateOk ==
  MatchState(step + 1)

TraceSpec ==
  TraceInit /\ [][TraceNext]_traceVars

====
