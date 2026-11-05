"""Copy for the onboarding wizard.

The prose lives here so the wizard page stays readable. One entry
per step: a title and a markdown body. Plus the prompt used as the
textarea placeholder on the profile step.

ADR-032.
"""

from __future__ import annotations

from genesis.onboarding import OnboardingStep

PROFILE_PROMPTS = "What you want to be called, a bit of background, your preferences. Markdown is fine."

STEP_COPY: dict[OnboardingStep, dict[str, str]] = {
    OnboardingStep.PURPOSE: {
        "title": "Welcome to Genesis Productivity",
        "body": (
            "Genesis Productivity keeps track of what you want to do, when you "
            "want to do it, and why. It records how you spend your time. If "
            "you want it to, it turns self-improvement into a lifelong game.\n\n"
            "It manages four kinds of things:\n\n"
            "- **Commitments**: tasks and projects.\n"
            "- **Plans and reflections**: journal entries.\n"
            "- **People**: your contact book.\n"
            "- **Time**: focus sessions.\n\n"
            "It is designed for you first and foremost. It is also the "
            "smoothest support your own AI assistant can have: your data "
            "stays in standard formats, so an assistant can read it and write "
            "to it.\n\n"
            "This onboarding introduces the kinds of data you will work with "
            "here, and gives you a place to put your own first content."
        ),
    },
    OnboardingStep.PROFILE: {
        "title": "A bit about yourself",
        "body": (
            "First, would you like to share a bit about yourself?\n\n"
            "Your profile is what your agent reads to work with you better: "
            "what you want to be called, a bit of background about yourself, "
            "your preferences.\n\n"
            "Write as much or as little as you are comfortable with. The text "
            "stays on the server where Genesis Productivity runs, and it is "
            "shared with your AI assistant.\n\n"
            "You can change it any time on the Profile page."
        ),
    },
    OnboardingStep.PROJECTS: {
        "title": "Projects",
        "body": (
            "A project is a desired outcome: planning a holiday, furnishing a "
            "kitchen. It can also be a large ongoing activity with no "
            "definite outcome, like learning.\n\n"
            "Think of a project as an envelope holding several smaller "
            "activities. Working on the activities makes progress on the "
            "project, and when they are all done the project reaches its "
            "conclusion.\n\n"
            "Two are worth starting with:\n\n"
            "- **Personal**, for your personal tasks.\n"
            "- **Learning**, for learning tasks.\n\n"
            "Add them with the button below."
        ),
    },
    OnboardingStep.TASKS: {
        "title": "Tasks",
        "body": (
            "A task is one specific commitment: one thing you can do. It may "
            "have a deadline.\n\n"
            "In Genesis Productivity a calendar appointment, a meeting for "
            "example, is a task too, with a scheduled start date and time.\n\n"
            "There is one date other systems do not have: the assigned date. "
            "It captures your decision to act on a task, independently of its "
            "deadline. A task is due next Friday; you assign it to Wednesday "
            "because that is the day you want to work on it. On Wednesday "
            "Genesis Productivity reminds you that you assigned yourself to it."
        ),
    },
    OnboardingStep.JOURNALS: {
        "title": "Journal",
        "body": (
            "Journal entries are long-form notes: information worth keeping "
            "that is not a task. Use them for your daily plan and reflection, "
            "for long-term plans for the month or the year, or for thoughts "
            "about a task or a project. Make them as simple or as elaborate "
            "as you like.\n\n"
            "Genesis Productivity supports daily, weekly, monthly, yearly, "
            "and misc entries.\n\n"
            "There is one entry per period. If you already wrote today's daily "
            "entry, Genesis Productivity will tell you to edit it instead of "
            "overwriting it."
        ),
    },
    OnboardingStep.CONTACTS: {
        "title": "Contacts",
        "body": (
            "Genesis Productivity keeps a contact book for you. Make it as "
            "sophisticated as you want: a contact can be related to tasks, so "
            "you can build a customer relationship management system, or it "
            "can just be a list of the people you know."
        ),
    },
}


__all__ = ["PROFILE_PROMPTS", "STEP_COPY"]
