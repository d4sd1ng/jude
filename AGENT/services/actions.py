from __future__ import annotations

from services.calendar import CalendarService
from services.coding import CodingService
from services.filesystem import write_external_after_confirmation
from services.home_assistant import HomeAssistantService
from services.mail import MailService


class ActionExecutor:
    def __init__(self):
        self.mail = MailService()
        self.coding = CodingService()
        self.calendar = CalendarService()
        self.home = HomeAssistantService()

    def __call__(self, action_type: str, payload: dict) -> str:
        if action_type == "mail_send":
            return self.mail.send_confirmed(**payload)
        if action_type == "mail_delete":
            return self.mail.delete_confirmed(**payload)
        if action_type == "git_merge":
            return self.coding.merge_confirmed(**payload)
        if action_type == "file_delete":
            return self.coding.delete_confirmed(**payload)
        if action_type == "external_write":
            return write_external_after_confirmation(**payload)
        if action_type == "calendar_create":
            return self.calendar.create_confirmed(**payload)
        # Vom Agenten vorgemerkte, sicherheitsrelevante Tool-Aktionen (Prompt-Injection-Schutz).
        if action_type == "home_switch":
            return str(self.home.switch_light(**payload))
        if action_type == "home_action":
            return str(self.home.run_profile(**payload))
        if action_type == "mail_archive":
            return str(self.mail.archive(**payload))
        if action_type == "code_write":
            return str(self.coding.write(**payload))
        if action_type == "code_commit":
            return str(self.coding.commit(**payload))
        if action_type == "code_push":
            return str(self.coding.push(**payload))
        if action_type == "code_pr":
            return str(self.coding.create_pr(**payload))
        if action_type == "code_branch":
            return str(self.coding.create_branch(**payload))
        if action_type == "code_clone":
            return str(self.coding.clone(**payload))
        if action_type == "code_pull":
            return str(self.coding.pull(**payload))
        raise ValueError(f"Unbekannter bestätigter Aktionstyp: {action_type}")
