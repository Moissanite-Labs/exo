---- MODULE SeatBinding ----
(*
  Abstract model of seated-core session binding and the TUI /new path (exo #939 B1).

  Real code:
  - Core seat load: src/exo/compose.py (load_seat), CoreEngine.__init__ (engine.py)
  - session.create seat rules: CoreRuntime._session_create (engine.py)
  - TUI resume + /new: clients/tui/exo_tui/app.py load_session, new_session
  - Reconnect: clients/tui/exo_tui/conn.py events(reconnect=True) — stutter; seats unchanged.
*)

EXTENDS Naturals, FiniteSets

CONSTANTS
  Seats,
  SessionPool,
  Fixed,
  MaxSessions

NoSeat == "NoSeat"
NoSession == "NoSession"
NoOutcome == "NoOutcome"

Outcome ==
  {"NoOutcome", "OkCreated", "OkResumed", "VisibleRefusal", "VisibleJoin", "SilentFail"}

VARIABLES
  coreSeat,
  liveSessions,
  foreignSessions,
  sessionSeat,
  clientLaunch,
  clientDisplay,
  clientSession,
  resumedOtherSeat,
  lastOutcome,
  fromClientNew,
  nextSid

TypeOK ==
  /\ coreSeat \in Seats \union {NoSeat}
  /\ liveSessions \subseteq SessionPool
  /\ foreignSessions \subseteq liveSessions
  /\ Cardinality(liveSessions) <= MaxSessions
  /\ sessionSeat \in [SessionPool -> Seats \union {NoSeat}]
  /\ \A s \in liveSessions : sessionSeat[s] \in Seats
  /\ \A s \in SessionPool \ liveSessions : sessionSeat[s] = NoSeat
  /\ clientLaunch \in Seats \union {NoSeat}
  /\ clientDisplay \in Seats \union {NoSeat}
  /\ clientSession \in SessionPool \union {NoSession}
  /\ (clientSession = NoSession) \/ (clientSession \in liveSessions)
  /\ resumedOtherSeat \in BOOLEAN
  /\ lastOutcome \in Outcome
  /\ fromClientNew \in BOOLEAN
  /\ nextSid \in SessionPool \ liveSessions

Init ==
  /\ coreSeat = NoSeat
  /\ liveSessions = {}
  /\ foreignSessions = {}
  /\ sessionSeat = [s \in SessionPool |-> NoSeat]
  /\ clientLaunch = NoSeat
  /\ clientDisplay = NoSeat
  /\ clientSession = NoSession
  /\ resumedOtherSeat = FALSE
  /\ lastOutcome = "NoOutcome"
  /\ fromClientNew = FALSE
  /\ nextSid \in SessionPool

Unused(s) == sessionSeat[s] = NoSeat

LoadCoreSeat(seat) ==
  /\ coreSeat = NoSeat
  /\ seat \in Seats
  /\ coreSeat' = seat
  /\ foreignSessions' = foreignSessions \union liveSessions
  /\ UNCHANGED <<liveSessions, sessionSeat, clientLaunch, clientDisplay,
                  clientSession, resumedOtherSeat, lastOutcome, fromClientNew, nextSid>>

ClientBoot(launch) ==
  /\ launch \in Seats \union {NoSeat}
  /\ clientLaunch' = launch
  /\ clientDisplay' = IF launch = NoSeat THEN clientDisplay ELSE launch
  /\ UNCHANGED <<coreSeat, liveSessions, foreignSessions, sessionSeat, clientSession,
                  resumedOtherSeat, lastOutcome, fromClientNew, nextSid>>

NormalizeSeat(req) ==
  IF coreSeat = NoSeat
  THEN IF req = NoSeat THEN NoSeat ELSE req
  ELSE IF req = NoSeat THEN coreSeat ELSE req

CoreRefuses(req) ==
  /\ coreSeat # NoSeat
  /\ req # NoSeat
  /\ req # coreSeat

AllocSession(seatValue, outcome, newClientSession, markClientNew) ==
  /\ nextSid \notin liveSessions
  /\ Unused(nextSid)
  /\ seatValue \in Seats
  /\ liveSessions' = liveSessions \union {nextSid}
  /\ sessionSeat' = [sessionSeat EXCEPT ![nextSid] = seatValue]
  /\ clientSession' = newClientSession
  /\ lastOutcome' = outcome
  /\ nextSid' \in SessionPool \ (liveSessions \union {nextSid})
  /\ fromClientNew' = markClientNew
  /\ UNCHANGED <<coreSeat, clientLaunch, clientDisplay, resumedOtherSeat, foreignSessions>>

(* Another core (or an earlier unseated core) already wrote this row in sessions.sqlite. *)
PersistSession(seat) ==
  /\ seat \in Seats
  /\ Cardinality(liveSessions) < MaxSessions
  /\ nextSid \notin liveSessions
  /\ Unused(nextSid)
  /\ liveSessions' = liveSessions \union {nextSid}
  /\ sessionSeat' = [sessionSeat EXCEPT ![nextSid] = seat]
  /\ foreignSessions' =
       IF coreSeat = NoSeat
       THEN foreignSessions
       ELSE IF seat = coreSeat
            THEN foreignSessions
            ELSE foreignSessions \union {nextSid}
  /\ nextSid' \in SessionPool \ (liveSessions \union {nextSid})
  /\ UNCHANGED <<coreSeat, clientLaunch, clientDisplay, clientSession,
                  resumedOtherSeat, lastOutcome, fromClientNew>>

SessionCreate(reqSeat) ==
  /\ Cardinality(liveSessions) < MaxSessions
  /\ IF CoreRefuses(reqSeat)
     THEN /\ lastOutcome' = "VisibleRefusal"
          /\ fromClientNew' = FALSE
          /\ UNCHANGED <<coreSeat, liveSessions, foreignSessions, sessionSeat, clientLaunch,
                          clientDisplay, clientSession, resumedOtherSeat, nextSid>>
     ELSE LET seatValue == NormalizeSeat(reqSeat)
          IN /\ seatValue \in Seats
             /\ AllocSession(seatValue, "OkCreated", nextSid, FALSE)

ResumeSession(sid) ==
  /\ sid \in liveSessions
  /\ clientSession' = sid
  /\ clientDisplay' =
       IF clientDisplay = NoSeat THEN sessionSeat[sid] ELSE clientDisplay
  /\ resumedOtherSeat' =
       (resumedOtherSeat \/ (coreSeat # NoSeat /\ sessionSeat[sid] # coreSeat))
  /\ lastOutcome' = "OkResumed"
  /\ fromClientNew' = FALSE
  /\ UNCHANGED <<coreSeat, liveSessions, foreignSessions, sessionSeat, clientLaunch, nextSid>>

ClientNew ==
  LET req == IF Fixed THEN clientLaunch ELSE clientDisplay
  IN /\ Cardinality(liveSessions) < MaxSessions
     /\ IF Fixed
        THEN IF CoreRefuses(req)
             THEN /\ coreSeat # NoSeat
                  /\ clientLaunch # NoSeat
                  /\ AllocSession(coreSeat, "VisibleJoin", nextSid, TRUE)
             ELSE AllocSession(NormalizeSeat(req), "OkCreated", nextSid, TRUE)
        ELSE IF CoreRefuses(req)
             THEN /\ lastOutcome' = "SilentFail"
                  /\ fromClientNew' = TRUE
                  /\ UNCHANGED <<coreSeat, liveSessions, foreignSessions, sessionSeat, clientLaunch,
                                  clientDisplay, clientSession, resumedOtherSeat, nextSid>>
             ELSE AllocSession(NormalizeSeat(req), "OkCreated", nextSid, TRUE)

vars == <<coreSeat, liveSessions, foreignSessions, sessionSeat, clientLaunch, clientDisplay,
           clientSession, resumedOtherSeat, lastOutcome, fromClientNew, nextSid>>

Reconnect ==
  /\ UNCHANGED vars

Next ==
  \/ \E seat \in Seats : LoadCoreSeat(seat)
  \/ \E launch \in Seats \union {NoSeat} : ClientBoot(launch)
  \/ \E seat \in Seats : PersistSession(seat)
  \/ \E req \in Seats \union {NoSeat} : SessionCreate(req)
  \/ \E sid \in SessionPool : ResumeSession(sid)
  \/ ClientNew
  \/ Reconnect

Spec ==
  Init /\ [][Next]_vars

SeatMatchesCore ==
  \A sid \in liveSessions :
    (coreSeat = NoSeat)
    \/ (sid \in foreignSessions)
    \/ (sessionSeat[sid] = coreSeat)

NoSilentRefusal ==
  lastOutcome # "SilentFail"

NewAfterResumeBindsThisCore ==
  ~(resumedOtherSeat /\ fromClientNew)
  \/ (lastOutcome = "VisibleRefusal")
  \/ (clientSession # NoSession /\ sessionSeat[clientSession] = coreSeat)

====
