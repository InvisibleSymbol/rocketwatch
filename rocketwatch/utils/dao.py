import math
import logging

from enum import IntEnum
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal, cast

import termplotlib as tpl
from eth_typing import ChecksumAddress

from utils import solidity
from utils.cfg import cfg
from utils.rocketpool import rp

log = logging.getLogger("dao")
log.setLevel(cfg["log_level"])


class DAO(ABC):
    def __init__(self, contract_name: str, proposal_contract_name: str):
        self.contract_name = contract_name
        self.contract = rp.get_contract_by_name(contract_name)
        self.proposal_contract = rp.get_contract_by_name(proposal_contract_name)

    @dataclass(frozen=True, slots=True)
    class Proposal(ABC):
        id: int
        proposer: ChecksumAddress
        message: str
        payload: bytes
        created: int

    @staticmethod
    @abstractmethod
    def fetch_proposal(proposal_id: int) -> Proposal:
        pass

    @abstractmethod
    def _build_vote_graph(self, proposal: Proposal) -> str:
        pass

    @staticmethod
    def sanitize(message: str) -> str:
        max_length = 150
        if len(message) > max_length:
            message = message[:(max_length - 1)] + "…"
        return message

    def build_proposal_body(
            self,
            proposal: Proposal,
            *,
            include_proposer=True,
            include_payload=True,
            include_votes=True
    ) -> str:
        body_repr = f"Description:\n{self.sanitize(proposal.message)}"

        if include_proposer:
            body_repr += f"\n\nProposed by:\n{proposal.proposer}"

        if include_payload:
            try:
                decoded = self.contract.decode_function_input(proposal.payload)
                function_name = decoded[0].function_identifier
                args = [f"  {arg} = {value}" for arg, value in decoded[1].items()]
                payload_str = f"{function_name}(\n" + "\n".join(args) + "\n)"
                body_repr += f"\n\nPayload:\n{payload_str}"
            except Exception:
                # if this goes wrong, just use the raw payload
                log.exception("Failed to decode proposal payload")
                body_repr += f"\n\nRaw Payload (failed to decode):\n{proposal.payload.hex()}"

        if include_votes:
            body_repr += f"\n\nVotes:\n{self._build_vote_graph(proposal)}"

        return body_repr


class DefaultDAO(DAO):
    def __init__(self, contract_name: Literal["rocketDAONodeTrustedProposals", "rocketDAOSecurityProposals"]):
        if contract_name == "rocketDAONodeTrustedProposals":
            self.display_name = "oDAO"
        elif contract_name == "rocketDAOSecurityProposals":
            self.display_name = "Security Council"
        else:
            raise ValueError("Unknown DAO")
        super().__init__(contract_name, "rocketDAOProposal")

    class ProposalState(IntEnum):
        Pending = 0
        Active = 1
        Cancelled = 2
        Defeated = 3
        Succeeded = 4
        Expired = 5
        Executed = 6

    @dataclass(frozen=True, slots=True)
    class Proposal(DAO.Proposal):
        start: int
        end: int
        expires: int
        votes_for: int
        votes_against: int
        votes_required: int

    def get_proposals_by_state(self) -> dict[ProposalState, list[int]]:
        num_proposals = self.proposal_contract.functions.getTotal().call()
        proposal_dao_names = [
            res.results[0] for res in rp.multicall.aggregate([
                self.proposal_contract.functions.getDAO(proposal_id) for proposal_id in range(1, num_proposals + 1)
            ]).results
        ]

        relevant_proposals = [(i+1) for (i, dao_name) in enumerate(proposal_dao_names) if (dao_name == self.contract_name)]
        proposal_states = [
            res.results[0] for res in rp.multicall.aggregate([
                self.proposal_contract.functions.getState(proposal_id) for proposal_id in relevant_proposals
            ]).results
        ]

        proposals = {state: [] for state in DefaultDAO.ProposalState}
        for proposal_id, state in zip(relevant_proposals, proposal_states):
            proposals[state].append(proposal_id)

        return proposals

    def fetch_proposal(self, proposal_id: int) -> Proposal:
        # map results of functions calls to function name
        multicall: dict[str, str | bytes | int] = {
            res.function_name: res.results[0] for res in rp.multicall.aggregate([
                self.proposal_contract.functions.getProposer(proposal_id),
                self.proposal_contract.functions.getMessage(proposal_id),
                self.proposal_contract.functions.getPayload(proposal_id),
                self.proposal_contract.functions.getCreated(proposal_id),
                self.proposal_contract.functions.getStart(proposal_id),
                self.proposal_contract.functions.getEnd(proposal_id),
                self.proposal_contract.functions.getExpires(proposal_id),
                self.proposal_contract.functions.getVotesFor(proposal_id),
                self.proposal_contract.functions.getVotesAgainst(proposal_id),
                self.proposal_contract.functions.getVotesRequired(proposal_id)
            ]).results
        }
        return DefaultDAO.Proposal(
            id=proposal_id,
            proposer=cast(ChecksumAddress, multicall["getProposer"]),
            message=multicall["getMessage"],
            payload=multicall["getPayload"],
            created=multicall["getCreated"],
            start=multicall["getStart"],
            end=multicall["getEnd"],
            expires=multicall["getExpires"],
            votes_for=solidity.to_int(multicall["getVotesFor"]),
            votes_against=solidity.to_int(multicall["getVotesAgainst"]),
            votes_required=solidity.to_float(multicall["getVotesRequired"])
        )

    def _build_vote_graph(self, proposal: Proposal) -> str:
        votes_for = proposal.votes_for
        votes_against = proposal.votes_against
        votes_required = math.ceil(proposal.votes_required)

        graph = tpl.figure()
        graph.barh(
            [votes_for, votes_against, max([votes_for, votes_against, votes_required])],
            ["For", "Against", ""],
            max_width=12
        )
        graph_bars = graph.get_string().split("\n")
        quorum_pct = round(100 * max(votes_for, votes_against) / votes_required)
        return (
            f"{graph_bars[0] : <{len(graph_bars[2])}}{'▏' if votes_for >= votes_against else ''}\n"
            f"{graph_bars[1] : <{len(graph_bars[2])}}{'▏' if votes_for <= votes_against else ''}\n"
            f"Quorum: {quorum_pct}%{' ✔' if quorum_pct >= 100 else ''}"
        )

class OracleDAO(DefaultDAO):
    def __init__(self):
        super().__init__("rocketDAONodeTrustedProposals")

class SecurityCouncil(DefaultDAO):
    def __init__(self):
        super().__init__("rocketDAOSecurityProposals")

class ProtocolDAO(DAO):
    def __init__(self):
        super().__init__("rocketDAOProtocolProposals", "rocketDAOProtocolProposal")

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

    @dataclass(frozen=True, slots=True)
    class Proposal(DAO.Proposal):
        start: int
        end_phase_1: int
        end_phase_2: int
        expires: int
        votes_for: float
        votes_against: float
        votes_veto: float
        votes_abstain: float
        quorum: float
        veto_quorum: float

        @property
        def votes_total(self):
            return self.votes_for + self.votes_against + self.votes_abstain

    def get_proposals_by_state(self) -> dict[ProposalState, list[int]]:
        num_proposals = self.proposal_contract.functions.getTotal().call()
        proposal_states = [
            res.results[0] for res in rp.multicall.aggregate([
                self.proposal_contract.functions.getState(proposal_id) for proposal_id in range(1, num_proposals + 1)
            ]).results
        ]

        proposals = {state: [] for state in ProtocolDAO.ProposalState}
        for proposal_id in range(1, num_proposals + 1):
            state = proposal_states[proposal_id - 1]
            proposals[state].append(proposal_id)

        return proposals


    def fetch_proposal(self, proposal_id: int) -> Proposal:
        # map results of functions calls to function name
        multicall: dict[str, str | bytes | int] = {
            res.function_name: res.results[0] for res in rp.multicall.aggregate([
                self.proposal_contract.functions.getProposer(proposal_id),
                self.proposal_contract.functions.getMessage(proposal_id),
                self.proposal_contract.functions.getPayload(proposal_id),
                self.proposal_contract.functions.getCreated(proposal_id),
                self.proposal_contract.functions.getStart(proposal_id),
                self.proposal_contract.functions.getPhase1End(proposal_id),
                self.proposal_contract.functions.getPhase2End(proposal_id),
                self.proposal_contract.functions.getExpires(proposal_id),
                self.proposal_contract.functions.getVotingPowerFor(proposal_id),
                self.proposal_contract.functions.getVotingPowerAgainst(proposal_id),
                self.proposal_contract.functions.getVotingPowerVeto(proposal_id),
                self.proposal_contract.functions.getVotingPowerAbstained(proposal_id),
                self.proposal_contract.functions.getVotingPowerRequired(proposal_id),
                self.proposal_contract.functions.getVetoQuorum(proposal_id)
            ]).results
        }
        return ProtocolDAO.Proposal(
            id=proposal_id,
            proposer=cast(ChecksumAddress, multicall["getProposer"]),
            message=multicall["getMessage"],
            payload=multicall["getPayload"],
            created=multicall["getCreated"],
            start=multicall["getStart"],
            end_phase_1=multicall["getPhase1End"],
            end_phase_2= multicall["getPhase2End"],
            expires=multicall["getExpires"],
            votes_for=solidity.to_float(multicall["getVotingPowerFor"]),
            votes_against=solidity.to_float(multicall["getVotingPowerAgainst"]),
            votes_veto=solidity.to_float(multicall["getVotingPowerVeto"]),
            votes_abstain=solidity.to_float(multicall["getVotingPowerAbstained"]),
            quorum=solidity.to_float(multicall["getVotingPowerRequired"]),
            veto_quorum=solidity.to_float(multicall["getVetoQuorum"])
        )

    def _build_vote_graph(self, proposal: Proposal) -> str:
        graph = tpl.figure()
        graph.barh(
            [
                round(proposal.votes_for),
                round(proposal.votes_against),
                round(proposal.votes_abstain),
                round(max(proposal.votes_total, proposal.quorum))
            ],
            ["For", "Against", "Abstain", ""],
            max_width=12
        )
        main_graph_repr = "\n".join(graph.get_string().split("\n")[:-1])

        graph = tpl.figure()
        graph.barh(
            [
                round(proposal.votes_veto),
                round(max(proposal.votes_veto, proposal.veto_quorum)),
            ],
            [f"{'Veto' : <{len('Against')}}", ""],
            max_width=12
        )
        veto_graph_bars = graph.get_string().split("\n")
        veto_graph_repr = f"{veto_graph_bars[0] : <{len(veto_graph_bars[1])}}▏"
        main_quorum_pct = round(100 * proposal.votes_total / proposal.quorum, 2)
        veto_quorum_pct = round(100 * proposal.votes_veto / proposal.veto_quorum, 2)
        return (
            f"{main_graph_repr}\n"
            f"Quorum: {main_quorum_pct}%{' ✔' if main_quorum_pct >= 100 else ''}\n\n"
            f"{veto_graph_repr}\n"
            f"Quorum: {veto_quorum_pct}%{' ✔' if veto_quorum_pct >= 100 else ''}"
        )
