"""Copy for the onboarding wizard.

The prose lives here so the wizard page stays readable. One entry
per step: a title and a markdown body. Plus the starter project
names and the two prompts used as the textarea placeholders on the
profile and the life story steps.

ADR-032.
"""

from __future__ import annotations

from genesis.onboarding import OnboardingStep

STARTER_PROJECTS: tuple[str, ...] = ("Personal", "Learning")
"""Projects offered on the projects step, preselected."""

PROFILE_PROMPTS = (
    "Your name, your timezone, what you do, and anything your assistant should know about you. Markdown is fine."
)

LIFE_STORY_PROMPTS = (
    "What shaped you: the experiences, the relationships, the turning points "
    "that explain where you are coming from. Markdown is fine."
)


STEP_COPY: dict[OnboardingStep, dict[str, str]] = {
    OnboardingStep.PURPOSE: {
        "title": "What Genesis is for",
        "body": (
            "Genesis keeps three kinds of record, and everything you put in "
            "them is readable and writable by you and by your own personal "
            "assistant. That is why tasks and appointments live in CalDAV, "
            "contacts live in CardDAV, and journal entries are plain markdown "
            "files you can open with any editor.\n\n"
            "**Commitments** live in tasks and projects. A task is one thing "
            "you have to do; a project is the envelope they belong to, or the "
            "outcome they are leading to.\n\n"
            "**Life records and reflection** live in the journal, plus the "
            "Profile and Life Story pages. The journal is anchored to a "
            "period: one entry per day, per week, per month, per year.\n\n"
            "**People** live in the contact book.\n\n"
            "The next five steps walk you through those five things once, so "
            "your account is not an empty shell when you get to it."
        ),
    },
    OnboardingStep.PROFILE: {
        "title": "Your profile",
        "body": (
            "Your profile is what your assistant reads to know who it works "
            "for. It is one markdown file on your account.\n\n"
            "Tell it your name, your timezone, what you do, and anything else "
            "worth knowing. You can edit it at any time from the Profile "
            "page, and your assistant reads it too.\n\n"
            "This step is optional. You can leave it empty and come back to "
            "it later."
        ),
    },
    OnboardingStep.LIFE_STORY: {
        "title": "Your life story",
        "body": (
            "The second file is the narrative history: what shaped you, the "
            "experiences, the relationships, and the turning points that "
            "explain where you are coming from.\n\n"
            "It is what lets your assistant respond with the right care when "
            "a conversation turns personal.\n\n"
            "It is optional, it stays private to your account, and you can "
            "edit or delete it at any time."
        ),
    },
    OnboardingStep.PROJECTS: {
        "title": "Projects",
        "body": (
            "A project is an envelope for tasks. Either because the tasks "
            "belong together, or because the project is the outcome and the "
            "tasks are the concrete actions leading to it.\n\n"
            "A project can carry a start date and a deadline, so the app can "
            "tell you what is overdue and what is coming.\n\n"
            "Two starter projects are preselected below. Untick what you do "
            "not want, or add your own."
        ),
    },
    OnboardingStep.TASKS: {
        "title": "Tasks",
        "body": (
            "A task is one specific commitment. It can carry up to three "
            "dates, and they mean different things:\n\n"
            "- **Scheduled start**: the task becomes an appointment and shows "
            "up on the Calendar page.\n"
            "- **Hard deadline**: a firm delivery date, surfaced on the "
            "Dashboard when it gets close.\n"
            "- **Assigned date**: the day you plan to act on it. This one is "
            "unique to Genesis: it lets you plan when to act without "
            "inventing a fake deadline.\n\n"
            "Add one real task below, using the same form the Tasks page "
            "uses."
        ),
    },
    OnboardingStep.JOURNALS: {
        "title": "Journal",
        "body": (
            "The journal holds one entry per period: one per day, one per "
            "week, one per month, one per year. The system refuses a second "
            "entry for the same period, so the journal always answers the "
            'question "what did I write on this day?".\n\n'
            "- **Daily** for morning planning, notes during the day, and "
            "reflection at night.\n"
            "- **Monthly** and **yearly** for long-horizon plans.\n"
            "- **Weekly** as the bridge between the monthly plan and the "
            "daily plan.\n\n"
            "The grid below shows which periods already have an entry."
        ),
    },
    OnboardingStep.CONTACTS: {
        "title": "Contacts",
        "body": (
            "The contact book holds the people and organisations in your "
            "life: names, emails, phone numbers, addresses, and notes.\n\n"
            "The contacts are stored in CardDAV, so your assistant can read "
            "them and add to them."
        ),
    },
}


__all__ = ["LIFE_STORY_PROMPTS", "PROFILE_PROMPTS", "STARTER_PROJECTS", "STEP_COPY"]
