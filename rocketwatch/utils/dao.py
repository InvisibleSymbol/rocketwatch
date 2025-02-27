import math
import logging

from enum import IntEnum
from abc import ABC, abstractmethod

import termplotlib as tpl

from utils import solidity
from utils.cfg import cfg
from utils.rocketpool import rp


log = logging.getLogger("dao")
log.setLevel(cfg["log_level"])


class DAO(ABC):
    def __init__(self, contract_name):
        self.contract_name = contract_name
        self.contract = rp.get_contract_by_name(contract_name)

    @staticmethod
    @abstractmethod
    def fetch_proposal(proposal_id: int) -> dict:
        pass

    @abstractmethod
    def _build_vote_graph(self, proposal: dict) -> str:
        pass

    @staticmethod
    def sanitize(message: str) -> str:
        max_length = 150
        suffix = "..."
        if len(message) > max_length:
            message = message[:max_length - len(suffix)] + suffix
        return message

    def build_proposal_body(
            self,
            proposal: dict,
            *,
            include_proposer=True,
            include_payload=True,
            include_votes=True
    ) -> str:
        body_repr = f"Description:\n{self.sanitize(proposal['message'])}"

        if include_proposer:
            body_repr += f"\n\nProposed by:\n{proposal['proposer']}"

        if include_payload:
            payload = proposal["payload"]
            try:
                decoded = self.contract.decode_function_input(payload)
                function_name = decoded[0].function_identifier
                args = [f"  {arg} = {value}" for arg, value in decoded[1].items()]
                payload_str = f"{function_name}(\n" + "\n".join(args) + "\n)"
                body_repr += f"\n\nPayload:\n{payload_str}"
            except Exception:
                # if this goes wrong, just use the raw payload
                log.exception("Failed to decode proposal payload")
                body_repr += f"\n\nRaw Payload (failed to decode):\n{payload.hex()}"

        if include_votes:
            body_repr += f"\n\nVotes:\n{self._build_vote_graph(proposal)}"

        return body_repr


class DefaultDAO(DAO):
    def __init__(self, contract_name: str):
        if contract_name == "rocketDAONodeTrustedProposals":
            self.display_name = "oDAO"
        elif contract_name == "rocketDAOSecurityProposals":
            self.display_name = "Security Council"
        else:
            raise ValueError("Unknown DAO")
        super().__init__(contract_name)

    class ProposalState(IntEnum):
        Pending = 0
        Active = 1
        Cancelled = 2
        Defeated = 3
        Succeeded = 4
        Expired = 5
        Executed = 6

    @staticmethod
    def fetch_proposal(proposal_id: int) -> dict:
        proposal_contract = rp.get_contract_by_name("rocketDAOProposal")
        # map results of functions calls to function name
        metadata_calls: dict[str, str | bytes | int] = {
            res.function_name: res.results[0] for res in rp.multicall.aggregate([
                proposal_contract.functions.getProposer(proposal_id),
                proposal_contract.functions.getMessage(proposal_id),
                proposal_contract.functions.getPayload(proposal_id),
                proposal_contract.functions.getCreated(proposal_id),
                proposal_contract.functions.getStart(proposal_id),
                proposal_contract.functions.getEnd(proposal_id),
                proposal_contract.functions.getExpires(proposal_id),
                proposal_contract.functions.getVotesFor(proposal_id),
                proposal_contract.functions.getVotesAgainst(proposal_id),
                proposal_contract.functions.getVotesRequired(proposal_id)
            ]).results
        }
        return {
            "id": proposal_id,
            "proposer": metadata_calls["getProposer"],
            "message": metadata_calls["getMessage"],
            "payload": metadata_calls["getPayload"],
            "created": metadata_calls["getCreated"],
            "start": metadata_calls["getStart"],
            "end": metadata_calls["getEnd"],
            "expires": metadata_calls["getExpires"],
            "votes_for": solidity.to_int(metadata_calls["getVotesFor"]),
            "votes_against": solidity.to_int(metadata_calls["getVotesAgainst"]),
            "votes_required": solidity.to_float(metadata_calls["getVotesRequired"])
        }

    def _build_vote_graph(self, proposal: dict) -> str:
        votes_for = proposal["votes_for"]
        votes_against = proposal["votes_against"]
        votes_required = math.ceil(proposal["votes_required"])

        graph = tpl.figure()
        graph.barh(
            [votes_for, votes_against, max([votes_for, votes_against, votes_required])],
            ["For", "Against", ""],
            max_width=20
        )
        graph_bars = graph.get_string().split("\n")
        quorum_pct = round(100 * max(votes_for, votes_against) / votes_required)
        return (
            f"{graph_bars[0] : <{len(graph_bars[2])}}{'▏' if votes_for >= votes_against else ''}\n"
            f"{graph_bars[1] : <{len(graph_bars[2])}}{'▏' if votes_against >= votes_for else ''}\n"
            f"Quorum: {quorum_pct}%{' ✔' if quorum_pct >= 100 else ''}"
        )


class ProtocolDAO(DAO):
    def __init__(self):
        super().__init__("rocketDAOProtocolProposals")

    class ProposalState(IntEnum):
        Pending = 0
        ActivePhase1 = 1
        ActivePhase2 = 2
        Destroyed = 3
        Vetoed = 4
        QuorumNotMet = 5
        Defeated = 6
        Succeeded = 7
        Expired = 8
        Executed = 9

    @staticmethod
    def fetch_proposal(proposal_id: int) -> dict:
        proposal_contract = rp.get_contract_by_name("rocketDAOProtocolProposal")
        # map results of functions calls to function name
        metadata_calls: dict[str, str | bytes | int] = {
            res.function_name: res.results[0] for res in rp.multicall.aggregate([
                proposal_contract.functions.getProposer(proposal_id),
                proposal_contract.functions.getMessage(proposal_id),
                proposal_contract.functions.getPayload(proposal_id),
                proposal_contract.functions.getCreated(proposal_id),
                proposal_contract.functions.getStart(proposal_id),
                proposal_contract.functions.getPhase1End(proposal_id),
                proposal_contract.functions.getPhase2End(proposal_id),
                proposal_contract.functions.getExpires(proposal_id),
                proposal_contract.functions.getVotingPowerFor(proposal_id),
                proposal_contract.functions.getVotingPowerAgainst(proposal_id),
                proposal_contract.functions.getVotingPowerVeto(proposal_id),
                proposal_contract.functions.getVotingPowerAbstained(proposal_id),
                proposal_contract.functions.getVotingPowerRequired(proposal_id),
                proposal_contract.functions.getVetoQuorum(proposal_id)
            ]).results
        }
        return {
            "id": proposal_id,
            "proposer": metadata_calls["getProposer"],
            "message": metadata_calls["getMessage"],
            "payload": metadata_calls["getPayload"],
            "created": metadata_calls["getCreated"],
            "start": metadata_calls["getStart"],
            "end_phase1": metadata_calls["getPhase1End"],
            "end_phase2": metadata_calls["getPhase2End"],
            "expires": metadata_calls["getExpires"],
            "votes_for": solidity.to_float(metadata_calls["getVotingPowerFor"]),
            "votes_against": solidity.to_float(metadata_calls["getVotingPowerAgainst"]),
            "votes_veto": solidity.to_float(metadata_calls["getVotingPowerVeto"]),
            "votes_abstain": solidity.to_float(metadata_calls["getVotingPowerAbstained"]),
            "quorum": solidity.to_float(metadata_calls["getVotingPowerRequired"]),
            "veto_quorum": solidity.to_float(metadata_calls["getVetoQuorum"])
        }

    def _build_vote_graph(self, proposal: dict) -> str:
        votes_total = proposal["votes_for"] + proposal["votes_against"] + proposal["votes_abstain"]

        graph = tpl.figure()
        graph.barh(
            [
                round(proposal["votes_for"]),
                round(proposal["votes_against"]),
                round(proposal["votes_abstain"]),
                round(max(votes_total, proposal["quorum"]))
            ],
            ["For", "Against", "Abstain", ""],
            max_width=20
        )
        main_graph_repr = "\n".join(graph.get_string().split("\n")[:-1])

        graph = tpl.figure()
        graph.barh(
            [
                round(proposal["votes_veto"]),
                round(max(proposal["votes_veto"], proposal["veto_quorum"]))
            ],
            [f"{'Veto' : <{len('Against')}}", ""],
            max_width=20
        )
        veto_graph_bars = graph.get_string().split("\n")
        veto_graph_repr = f"{veto_graph_bars[0] : <{len(veto_graph_bars[1])}}▏"
        main_quorum_pct = round(100 * votes_total / proposal["quorum"], 2)
        veto_quorum_pct = round(100 * proposal["votes_veto"] / proposal["veto_quorum"], 2)
        return (
            f"{main_graph_repr}\n"
            f"Quorum: {main_quorum_pct}%{' ✔' if main_quorum_pct >= 100 else ''}\n\n"
            f"{veto_graph_repr}\n"
            f"Quorum: {veto_quorum_pct}%{' ✔' if veto_quorum_pct >= 100 else ''}"
        )
