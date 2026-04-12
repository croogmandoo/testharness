import json
import xml.etree.ElementTree as ET
from datetime import datetime


def generate_task_xml(task_name: str, harness_url: str,
                      app: str | None, environment: str,
                      interval_minutes: int = 60) -> str:
    """
    Generate Windows Task Scheduler XML that calls POST /api/runs on an interval.

    Usage:
        xml = generate_task_xml("Harness-Prod", "http://localhost:8000",
                                app=None, environment="production", interval_minutes=60)
        with open("task.xml", "w") as f:
            f.write(xml)
        # Then: schtasks /create /xml task.xml /tn "Harness-Prod"
    """
    payload: dict = {"environment": environment, "triggered_by": "api"}
    if app:
        payload["app"] = app
    body = json.dumps(payload)
    command = (
        f'powershell -Command "Invoke-RestMethod -Uri {harness_url}/api/runs '
        f"-Method POST -ContentType 'application/json' -Body '{body}'\""
    )

    root = ET.Element("Task", version="1.2",
                      xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task")
    reg_info = ET.SubElement(root, "RegistrationInfo")
    ET.SubElement(reg_info, "URI").text = f"\\{task_name}"
    triggers = ET.SubElement(root, "Triggers")
    trigger = ET.SubElement(triggers, "TimeTrigger")
    ET.SubElement(trigger, "StartBoundary").text = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    ET.SubElement(trigger, "Enabled").text = "true"
    repetition = ET.SubElement(trigger, "Repetition")
    ET.SubElement(repetition, "Interval").text = f"PT{interval_minutes}M"
    ET.SubElement(repetition, "Duration").text = "P1Y"

    actions = ET.SubElement(root, "Actions", Context="Author")
    exec_elem = ET.SubElement(actions, "Exec")
    ET.SubElement(exec_elem, "Command").text = "cmd.exe"
    ET.SubElement(exec_elem, "Arguments").text = f"/c {command}"

    settings = ET.SubElement(root, "Settings")
    ET.SubElement(settings, "MultipleInstancesPolicy").text = "IgnoreNew"
    ET.SubElement(settings, "Enabled").text = "true"

    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def print_setup_instructions(harness_url: str, environment: str,
                              interval_minutes: int = 60) -> None:
    xml = generate_task_xml(
        task_name=f"Harness-{environment}",
        harness_url=harness_url,
        app=None,
        environment=environment,
        interval_minutes=interval_minutes,
    )
    filename = f"harness-task-{environment}.xml"
    with open(filename, "w") as f:
        f.write(xml)
    print(f"Task XML written to {filename}")
    print(f"To install: schtasks /create /xml {filename} /tn Harness-{environment}")
