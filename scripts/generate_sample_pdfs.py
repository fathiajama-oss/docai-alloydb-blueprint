# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0

"""Generates 3 synthetic multi-page enterprise PDFs in sample_docs/ using ReportLab."""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "sample_docs"


def _build_styles() -> dict[str, ParagraphStyle]:
    """Creates crisp, minimalist typography styles for enterprise PDF documents."""
    base = getSampleStyleSheet()
    return {
        "kicker": ParagraphStyle(
            "Kicker",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8.5,
            leading=12,
            textColor=colors.HexColor("#0A4D31"),
            spaceAfter=6,
        ),
        "doc_title": ParagraphStyle(
            "DocTitle",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=20,
            leading=25,
            alignment=0,
            textColor=colors.HexColor("#111111"),
            spaceAfter=10,
        ),
        "h1": ParagraphStyle(
            "SectionH1",
            parent=base["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=14,
            leading=18,
            textColor=colors.HexColor("#111111"),
            spaceBefore=10,
            spaceAfter=8,
        ),
        "h2": ParagraphStyle(
            "SectionH2",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=11.5,
            leading=15,
            textColor=colors.HexColor("#1F2937"),
            spaceBefore=8,
            spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "Body",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=10,
            leading=14.5,
            textColor=colors.HexColor("#222524"),
            spaceAfter=8,
        ),
        "bullet": ParagraphStyle(
            "Bullet",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=10,
            leading=14.5,
            leftIndent=16,
            textColor=colors.HexColor("#222524"),
            spaceAfter=5,
        ),
        "table_cell": ParagraphStyle(
            "TableCell",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9.2,
            leading=13,
            textColor=colors.HexColor("#111111"),
        ),
        "table_header": ParagraphStyle(
            "TableHeader",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=9.2,
            leading=13,
            textColor=colors.white,
        ),
    }


def _styled_table(rows: list[list[str]], col_widths: list[float], styles: dict[str, ParagraphStyle]) -> Table:
    """Renders a clean architectural ReportLab table with crisp borders."""
    formatted_rows: list[list[Paragraph]] = []
    for idx, row in enumerate(rows):
        cell_style = styles["table_header"] if idx == 0 else styles["table_cell"]
        formatted_rows.append([Paragraph(cell, cell_style) for cell in row])

    table = Table(formatted_rows, colWidths=col_widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#111111")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7F8F7")]),
                ("GRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#D2D7D4")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return table


def _make_page_footer(doc_code: str):
    """Creates a clean page footer callback showing document identifier and page number."""
    def _draw_footer(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 8.5)
        canvas.setFillColor(colors.HexColor("#6C7570"))
        canvas.drawString(0.8 * inch, 0.52 * inch, f"SYNTHETIC ENTERPRISE SPECIMEN | {doc_code}")
        canvas.drawRightString(letter[0] - 0.8 * inch, 0.52 * inch, f"Page {doc.page} of 3")
        canvas.restoreState()

    return _draw_footer


def _build_security_pdf(output_path: Path, styles: dict[str, ParagraphStyle]) -> None:
    """Generates Cloud_Security_and_Governance_Standard.pdf (3 pages)."""
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=letter,
        leftMargin=0.8 * inch,
        rightMargin=0.8 * inch,
        topMargin=0.8 * inch,
        bottomMargin=0.85 * inch,
        title="Cloud Security and Governance Standard",
    )

    story = [
        # Page 1
        Paragraph("ENTERPRISE GOVERNANCE SPECIFICATION | DOC-SEC-2026", styles["kicker"]),
        Paragraph("Cloud Security and Governance Standard", styles["doc_title"]),
        Paragraph(
            "This synthetic reference standard defines baseline zero-trust networking, "
            "cryptographic key management, and incident response service-level agreements "
            "for enterprise workloads running on managed cloud infrastructure.",
            styles["body"],
        ),
        Paragraph("1. Executive Scope &amp; Zero-Trust Network Segmentation", styles["h1"]),
        Paragraph(
            "All production data stores, analytical engines, and document processing "
            "pipelines must operate inside isolated Virtual Private Cloud (VPC) perimeters. "
            "Direct public ingress to relational databases or internal microservice ports is "
            "strictly prohibited.",
            styles["body"],
        ),
        Paragraph("1.1 Mandatory Perimeter &amp; Transport Controls", styles["h2"]),
        Paragraph(
            "- <b>VPC Service Perimeters:</b> Enforce context-aware security perimeters around "
            "object storage buckets, document extraction endpoints, and database clusters to "
            "prevent unauthorized data exfiltration.",
            styles["bullet"],
        ),
        Paragraph(
            "- <b>Private Service Access &amp; Auth Proxy:</b> Relational traffic must traverse "
            "private VPC peering or an identity-authenticated local proxy tunnel using "
            "short-lived IAM credentials.",
            styles["bullet"],
        ),
        Paragraph(
            "- <b>Mutual TLS (mTLS) Enforcement:</b> East-west service-to-service calls "
            "between ingestion workers, vectorization APIs, and serving tiers require "
            "workload-identity mTLS verification on every connection.",
            styles["bullet"],
        ),
        Paragraph(
            "- <b>Least-Privilege Service Accounts:</b> Ingestion jobs may only write to "
            "designated staging prefixes and insert into approved database schemas.",
            styles["bullet"],
        ),
        PageBreak(),

        # Page 2
        Paragraph("SECTION 02 | CRYPTOGRAPHIC CONTROLS", styles["kicker"]),
        Paragraph("2. Cryptographic Standards &amp; Key Rotation Policy", styles["h1"]),
        Paragraph(
            "Sensitive operational artifacts, structured PII tables, and vector embedding "
            "indexes require defense-in-depth encryption at rest and in transit using "
            "centrally governed cryptographic keys.",
            styles["body"],
        ),
        Paragraph("2.1 Customer-Managed Encryption Keys (CMEK)", styles["h2"]),
        Paragraph(
            "CMEK is mandatory for all Tier-0 and Tier-1 data stores, including object "
            "storage buckets holding raw source documents, normalized JSON outputs, and "
            "managed PostgreSQL clusters. Keys must reside in hardware-backed key rings "
            "within the same geographic region as the storage resource.",
            styles["body"],
        ),
        Paragraph("2.2 Automated Key Rotation Schedule", styles["h2"]),
        Paragraph(
            "- <b>Operational Secrets &amp; API Keys:</b> Automated key rotation is mandatory "
            "every <b>90 days</b> for operational secrets, service credentials, and active "
            "database encryption keys.",
            styles["bullet"],
        ),
        Paragraph(
            "- <b>Cold Archive &amp; Compliance Vault Keys:</b> Automated key rotation is "
            "enforced every <b>365 days</b> for immutable cold archive keys and long-term "
            "audit backup buckets.",
            styles["bullet"],
        ),
        Paragraph(
            "- <b>Transport Layer Security:</b> External and internal TLS endpoints must "
            "require <b>TLS 1.3 minimum</b>, disabling legacy cipher suites and unencrypted "
            "fallback listeners.",
            styles["bullet"],
        ),
        Paragraph(
            "- <b>Key Revocation Readiness:</b> Compromised key versions must be disabled "
            "within 15 minutes via automated security orchestration playbooks.",
            styles["bullet"],
        ),
        PageBreak(),

        # Page 3
        Paragraph("SECTION 03 | INCIDENT RESPONSE GOVERNANCE", styles["kicker"]),
        Paragraph("3. Security Incident Response &amp; SLA Matrix", styles["h1"]),
        Paragraph(
            "Security Operations Center (SOC) responders and platform engineering on-call "
            "rotations adhere to the following binding Service Level Agreements (SLAs) "
            "across severity tiers:",
            styles["body"],
        ),
        Spacer(1, 0.1 * inch),
        _styled_table(
            rows=[
                ["Severity Tier", "Classification Criteria", "Acknowledgement SLA", "Containment / Resolution SLA"],
                [
                    "Sev-1 Critical",
                    "Confirmed production data exposure, unauthorized key access, or active perimeter breach.",
                    "15 minutes",
                    "4 hours containment",
                ],
                [
                    "Sev-2 High",
                    "Isolated service degradation, failed CMEK rotation, or single-tenant policy violation.",
                    "1 hour",
                    "24 hours containment",
                ],
                [
                    "Sev-3 Moderate",
                    "Non-production misconfiguration, stale audit log alert, or minor posture drift.",
                    "8 hours",
                    "5 business days resolution",
                ],
            ],
            col_widths=[1.25 * inch, 2.55 * inch, 1.45 * inch, 1.65 * inch],
            styles=styles,
        ),
        Spacer(1, 0.18 * inch),
        Paragraph("3.1 Post-Incident Verification &amp; Audit Retention", styles["h2"]),
        Paragraph(
            "Every Sev-1 and Sev-2 incident requires a blameless root-cause review "
            "published within 48 hours of containment, accompanied by immutable audit logs "
            "retained for at least 400 days.",
            styles["body"],
        ),
    ]

    footer = _make_page_footer("DOC-SEC-2026")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def _build_industrial_sop_pdf(output_path: Path, styles: dict[str, ParagraphStyle]) -> None:
    """Generates Industrial_Equipment_Maintenance_SOP.pdf (3 pages)."""
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=letter,
        leftMargin=0.8 * inch,
        rightMargin=0.8 * inch,
        topMargin=0.8 * inch,
        bottomMargin=0.85 * inch,
        title="Industrial Equipment Maintenance SOP",
    )

    story = [
        # Page 1
        Paragraph("MANUFACTURING RELIABILITY STANDARD | SOP-HPC-5000", styles["kicker"]),
        Paragraph("Industrial Equipment Maintenance SOP", styles["doc_title"]),
        Paragraph(
            "Standard Operating Procedure for preventive inspection, telemetry monitoring, "
            "and safe mechanical isolation of High-Pressure Coolant Pump assembly "
            "<b>HPC-5000</b> across automated machining cells.",
            styles["body"],
        ),
        Paragraph("1. High-Pressure Coolant Pump (HPC-5000) Operational Envelope", styles["h1"]),
        Paragraph(
            "Pump HPC-5000 supplies temperature-stabilized synthetic coolant to primary "
            "spindle manifolds. Operators and reliability technicians must verify steady-state "
            "hydraulic parameters at the beginning of every shift.",
            styles["body"],
        ),
        Paragraph("1.1 Nominal Operating Parameters &amp; Filtration Checks", styles["h2"]),
        Paragraph(
            "- <b>Nominal Flow Rate:</b> <b>420 L/min</b> under steady production load "
            "(acceptable control band 400 to 440 L/min).",
            styles["bullet"],
        ),
        Paragraph(
            "- <b>Operating Discharge Pressure:</b> <b>18.5 bar</b> at manifold inlet "
            "transducer PT-104.",
            styles["bullet"],
        ),
        Paragraph(
            "- <b>Duplex Filtration Checks:</b> Inspect differential pressure across Bank A "
            "and Bank B 10-micron cartridge housings every 12 hours; switch to standby bank "
            "when differential pressure exceeds 1.8 bar.",
            styles["bullet"],
        ),
        Paragraph(
            "- <b>Cavitation Prevention:</b> Maintain reservoir sump level above 78 percent "
            "and verify suction strainer vacuum remains below 0.2 bar.",
            styles["bullet"],
        ),
        PageBreak(),

        # Page 2
        Paragraph("SECTION 02 | PREDICTIVE TELEMETRY LIMITS", styles["kicker"]),
        Paragraph("2. Vibration &amp; Bearing Thermal Thresholds", styles["h1"]),
        Paragraph(
            "Tri-axial accelerometers mounted on the drive-end and non-drive-end bearing "
            "housings continuously evaluate RMS vibration velocity against ISO 10816 "
            "severity zones.",
            styles["body"],
        ),
        Spacer(1, 0.08 * inch),
        _styled_table(
            rows=[
                ["ISO 10816 Zone", "RMS Velocity Range", "Machine Condition", "Mandatory Operator Action"],
                [
                    "Zone A Optimal",
                    "Below 2.3 mm/s",
                    "Newly commissioned or balanced state",
                    "Continue normal automated telemetry logging.",
                ],
                [
                    "Zone B Acceptable",
                    "2.3 - 4.5 mm/s",
                    "Unrestricted long-term operation",
                    "Monitor weekly spectral trend; no intervention needed.",
                ],
                [
                    "Zone C Alert",
                    "4.5 - 7.1 mm/s",
                    "Early bearing raceway wear or misalignment",
                    "Schedule bearing inspection within 48 hours.",
                ],
                [
                    "Zone D Emergency Trip",
                    "Greater than 7.1 mm/s",
                    "Severe mechanical fault / imminent failure",
                    "Automatic line halt triggered; initiate LOTO immediately.",
                ],
            ],
            col_widths=[1.35 * inch, 1.45 * inch, 1.9 * inch, 2.2 * inch],
            styles=styles,
        ),
        Spacer(1, 0.16 * inch),
        Paragraph("2.1 Bearing Thermal Limits", styles["h2"]),
        Paragraph(
            "- <b>Bearing Temperature Warning:</b> PLC alarm latches at <b>82 C</b>, "
            "notifying the shift reliability engineer to verify lubrication flow.",
            styles["bullet"],
        ),
        Paragraph(
            "- <b>Automatic Thermal Trip:</b> Hardwired safety relay executes an "
            "automatic line halt at <b>95 C</b> to prevent rotor seizure.",
            styles["bullet"],
        ),
        PageBreak(),

        # Page 3
        Paragraph("SECTION 03 | HAZARDOUS ENERGY ISOLATION", styles["kicker"]),
        Paragraph("3. 5-Step Lockout/Tagout (LOTO) &amp; Return-to-Service Procedure", styles["h1"]),
        Paragraph(
            "Before opening coupling guards, replacing mechanical seals, or servicing "
            "bearings on Pump HPC-5000, authorized technicians must execute the following "
            "five mandatory steps in strict sequence:",
            styles["body"],
        ),
        Paragraph(
            "1. <b>Isolate Main Breaker:</b> Command controlled stop at HMI panel, then "
            "rotate local disconnect switch MCC-4B to the OFF position.",
            styles["bullet"],
        ),
        Paragraph(
            "2. <b>Bleed Hydraulic Manifold to 0 bar:</b> Open manual needle bleed valve "
            "HV-209 slowly until manifold gauge PT-104 reads exactly <b>0 bar</b> and "
            "accumulator drain flow stops.",
            styles["bullet"],
        ),
        Paragraph(
            "3. <b>Apply Dual Physical Padlock &amp; Tag:</b> Install dual personal safety "
            "padlocks and signed LOTO tags onto both the electrical breaker hasp and the "
            "discharge isolation valve.",
            styles["bullet"],
        ),
        Paragraph(
            "4. <b>Verify Zero-Energy Start Test:</b> Attempt a local jog start from the "
            "control station and confirm zero shaft rotation and zero residual voltage "
            "using a calibrated multimeter.",
            styles["bullet"],
        ),
        Paragraph(
            "5. <b>Laser Shaft Alignment Check Before Restart:</b> Prior to removing locks "
            "and returning the unit to service, perform a precision laser shaft alignment "
            "check confirming angular and parallel offset within <b>0.05 mm</b>.",
            styles["bullet"],
        ),
        Spacer(1, 0.1 * inch),
        Paragraph(
            "Upon completion of Step 5, log calibration readings in the maintenance ledger "
            "and run a 10-minute unloaded observation check before resuming full flow.",
            styles["body"],
        ),
    ]

    footer = _make_page_footer("SOP-HPC-5000")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def _build_reliability_playbook_pdf(output_path: Path, styles: dict[str, ParagraphStyle]) -> None:
    """Generates Enterprise_Platform_Reliability_Playbook.pdf (3 pages)."""
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=letter,
        leftMargin=0.8 * inch,
        rightMargin=0.8 * inch,
        topMargin=0.8 * inch,
        bottomMargin=0.85 * inch,
        title="Enterprise Platform Reliability Playbook",
    )

    story = [
        # Page 1
        Paragraph("SITE RELIABILITY ARCHITECTURE | SRE-REL-2026", styles["kicker"]),
        Paragraph("Enterprise Platform Reliability Playbook", styles["doc_title"]),
        Paragraph(
            "Architectural blueprint and operational governance guide for high-availability "
            "database tiers, automated regional failover, and continuous backup verification "
            "across mission-critical enterprise platforms.",
            styles["body"],
        ),
        Paragraph("1. Multi-Region High Availability Architecture", styles["h1"]),
        Paragraph(
            "Enterprise services rely on active-passive regional database topologies paired "
            "with horizontally scalable stateless application tiers. Storage durability is "
            "decoupled from compute instances so node replacements complete rapidly without "
            "full data re-hydration.",
            styles["body"],
        ),
        Paragraph("1.1 Core Availability Mechanisms", styles["h2"]),
        Paragraph(
            "- <b>Synchronous Regional Commit:</b> Primary database writes commit "
            "synchronously across multiple availability zones within the primary region "
            "before acknowledging client transactions.",
            styles["bullet"],
        ),
        Paragraph(
            "- <b>Cross-Region Read Replicas:</b> Asynchronous read-replica clusters in a "
            "secondary region absorb analytical vector queries and provide warm disaster "
            "recovery promotion targets.",
            styles["bullet"],
        ),
        Paragraph(
            "- <b>Automated Health-Check Routing:</b> Global load balancers probe health "
            "endpoints every 5 seconds and drain traffic away from degraded zones "
            "automatically without manual DNS edits.",
            styles["bullet"],
        ),
        PageBreak(),

        # Page 2
        Paragraph("SECTION 02 | RECOVERY SERVICE OBJECTIVES", styles["kicker"]),
        Paragraph("2. Tiered Disaster Recovery Objectives (RPO &amp; RTO Matrix)", styles["h1"]),
        Paragraph(
            "Every production application is assigned a reliability tier during architecture "
            "review. Recovery Point Objective (RPO) defines maximum tolerable data loss, "
            "while Recovery Time Objective (RTO) bounds service restoration duration:",
            styles["body"],
        ),
        Spacer(1, 0.1 * inch),
        _styled_table(
            rows=[
                ["Service Tier", "Workload Examples", "Recovery Point Objective (RPO)", "Recovery Time Objective (RTO)"],
                [
                    "Tier-0 Mission Critical",
                    "Core payment ledgers, identity verification, and safety telemetry",
                    "Zero data loss (0 sec)",
                    "Under 60 seconds",
                ],
                [
                    "Tier-1 Core Business",
                    "Customer portals, document ingestion pipelines, and CRM sync",
                    "Under 5 minutes",
                    "Under 15 minutes",
                ],
                [
                    "Tier-2 Internal Analytics",
                    "Batch reporting warehouses and offline sandbox environments",
                    "Under 4 hours",
                    "Under 24 hours",
                ],
            ],
            col_widths=[1.45 * inch, 2.2 * inch, 1.65 * inch, 1.6 * inch],
            styles=styles,
        ),
        Spacer(1, 0.16 * inch),
        Paragraph("2.1 Regional Failover Readiness Criteria", styles["h2"]),
        Paragraph(
            "Tier-0 platforms must maintain pre-provisioned compute capacity in the standby "
            "region and pass automated game-day promotion tests each quarter without "
            "manual schema intervention.",
            styles["body"],
        ),
        PageBreak(),

        # Page 3
        Paragraph("SECTION 03 | BACKUP & CHANGE GOVERNANCE", styles["kicker"]),
        Paragraph("3. Continuous Backup Retention &amp; Change-Freeze Governance", styles["h1"]),
        Paragraph(
            "Resilience against both infrastructure faults and logical operator errors "
            "requires continuous transaction log archiving combined with disciplined "
            "release windows.",
            styles["body"],
        ),
        Paragraph("3.1 Point-in-Time Recovery &amp; Restore Drills", styles["h2"]),
        Paragraph(
            "- <b>35-Day Continuous PITR Window:</b> Database clusters retain continuous "
            "write-ahead logs and automated snapshots covering a rolling <b>35-day</b> "
            "point-in-time recovery window.",
            styles["bullet"],
        ),
        Paragraph(
            "- <b>Weekly Automated Restore Drills:</b> Every Wednesday at 03:00 UTC, "
            "an automated pipeline restores a clone into an isolated verification project, "
            "checks vector index integrity, and logs restore duration.",
            styles["bullet"],
        ),
        Paragraph("3.2 Production Change-Freeze Windows", styles["h2"]),
        Paragraph(
            "- <b>Standard Weekend Freeze:</b> Non-emergency schema migrations, network "
            "routing changes, and production application rollouts are prohibited during the "
            "weekly change-freeze window from <b>Friday 18:00 UTC to Monday 06:00 UTC</b>.",
            styles["bullet"],
        ),
        Paragraph(
            "- <b>Emergency Exception Path:</b> Urgent security mitigations during a "
            "change freeze require dual approval from the Incident Commander and Platform "
            "Architecture lead.",
            styles["bullet"],
        ),
    ]

    footer = _make_page_footer("SRE-REL-2026")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def generate_all(output_dir: Path = DEFAULT_OUTPUT_DIR) -> list[Path]:
    """Generates all three multi-page synthetic PDF specimens."""
    output_dir.mkdir(parents=True, exist_ok=True)
    styles = _build_styles()

    targets = [
        (output_dir / "Cloud_Security_and_Governance_Standard.pdf", _build_security_pdf),
        (output_dir / "Industrial_Equipment_Maintenance_SOP.pdf", _build_industrial_sop_pdf),
        (output_dir / "Enterprise_Platform_Reliability_Playbook.pdf", _build_reliability_playbook_pdf),
    ]

    generated: list[Path] = []
    for pdf_path, builder in targets:
        builder(pdf_path, styles)
        generated.append(pdf_path)
    return generated


if __name__ == "__main__":
    for path in generate_all():
        print(f"Generated {path.name} ({path.stat().st_size} bytes)")
