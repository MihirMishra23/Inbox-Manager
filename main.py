from __future__ import print_function

import os
import os.path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from dotenv import load_dotenv
import openai
import pandas as pd
from datetime import datetime
import time

from utils import *
from command import *

# If modifying these scopes, delete the file token.json.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")


def connect(
    token_json_path: str = "token.json", cred_json_path: str = "credentials.json"
):
    """
    Returns credential to connect to API. This cred object can be used to build
    API resources.

    :param token_json_path: The path to the token json. If already filled,
        doesn't change
    :param cred_json_path: The path to the credential file. This is exported from google.
    """
    creds = None
    # The file token.json stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first
    # time.
    if os.path.exists(token_json_path):
        creds = Credentials.from_authorized_user_file(token_json_path, SCOPES)
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(cred_json_path, SCOPES)
            creds = flow.run_local_server(port=0)
        # Save the credentials for the next run
        with open(token_json_path, "w") as token:
            token.write(creds.to_json())
    return creds


def search_emails_and_update_sheet(
    gmail_service, sheets_service, sheet_id: str, contact_df: pd.DataFrame, query: str
):
    """
    Populates the Emails tab based on all listed contacts in the Contacts tab.
    """
    # for contact_address in contact_df["contact info"]:
    results = search_threads(gmail_service, query=query)
    # for each email to/from a contact, read it (output plain/text to sheet)
    for msg in results:
        # read message
        message = read_message(gmail_service, msg)
        message.To = extract_substring(message.To)
        message.From = extract_substring(message.From)
        if message.Cc:
            message.Cc = extract_substring(message.Cc)

        # collect contact ids
        tos = message.To.split(", ")
        ids: list = []
        for To in tos:
            ids.extend(
                contact_df.index[contact_df["contact info"].str.contains(To)].to_list()
            )
        ids.extend(
            contact_df.index[
                contact_df["contact info"].str.contains(message.From)
            ].to_list()
        )
        if message.Cc:
            ccs = message.Cc.split(", ")
            for cc in ccs:
                ids.extend(
                    contact_df.index[
                        contact_df["contact info"].str.contains(cc)
                    ].to_list()
                )

        # write to Emails tab if the email isn't already in it
        if len(ids) > 0:
            emails = read_sheet(sheets_service, sheet_id, range="Emails!A:E")
            email_df = pd.DataFrame(emails[1:], columns=emails[0])
            email_df = email_df.set_index("ID")

            if message.__str__(hide_date=True) not in email_df["content"].to_list():
                parsed_date = datetime.strptime(
                    message.Date, "%a, %d %b %Y %H:%M:%S %z"
                )
                formatted_date = parsed_date.strftime("%m/%d/%Y %I:%M %p")

                row_data = [
                    ", ".join(ids),
                    formatted_date,
                    ", ".join(contact_df.loc[ids, "name"].to_list()),
                    None,
                    message.__str__(hide_date=True),
                ]
                add_row(
                    sheets_service,
                    sheet_id=sheet_id,
                    row_data=row_data,
                    tab_name="Emails",
                )

                # update last contacted on date in Contacts tab
                for id in ids:
                    if (
                        contact_df.at[id, "last contacted on"] is not None
                        or datetime.strptime(
                            contact_df.at[id, "last contacted on"], "%m/%d/%Y"
                        )
                        < parsed_date
                    ):
                        update_cell(
                            sheets_service,
                            cell_value=parsed_date.strftime("%m/%d/%Y"),
                            cell_loc=f"E{int(id)+1}",
                            sheet_id=sheet_id,
                            tab_name="Contacts",
                        )


def main():
    gmail_creds = connect(
        token_json_path="gmail_token.json", cred_json_path=GMAIL_CREDENTIALS_PATH
    )
    drive_creds = connect(
        token_json_path="drive_token.json", cred_json_path=DRIVE_CREDENTIALS_PATH
    )

    try:
        gmail_service = build("gmail", "v1", credentials=gmail_creds)
        drive_service = build("drive", "v3", credentials=drive_creds)
        sheets_service = build("sheets", "v4", credentials=drive_creds)

        sheet_id = search_drive(drive_service, name=SHEET_NAME, file_type="sheet")

        contact_vals = read_sheet(sheets_service, sheet_id, range="Contacts!A:I")

        contact_df = pd.DataFrame(contact_vals[1:], columns=contact_vals[0])
        contact_df = contact_df.set_index("ID")

        if not INITIALIZED:
            for contact_address in contact_df["contact info"]:
                address = contact_address.split("\n")[0]
                search_emails_and_update_sheet(
                    gmail_service=gmail_service,
                    sheets_service=sheets_service,
                    sheet_id=sheet_id,
                    contact_df=contact_df,
                    query=f"to:{address} OR from:{address}",
                )
                contact_vals = read_sheet(
                    sheets_service, sheet_id, range="Contacts!A:I"
                )

                contact_df = pd.DataFrame(contact_vals[1:], columns=contact_vals[0])
                contact_df = contact_df.set_index("ID")
                time.sleep(5)
            # change the value of INITIALIZED in command.py from False to True
            with open("command.py", "r") as f:
                content = f.read()
                content = content.replace("INITIALIZED = False", "INITIALIZED = True")
                with open("command.py", "w") as f:
                    f.write(content)
            return
        with open("log.txt", "r+") as file:
            log = file.read()
            last_run_date = log.split("\n")[-1]
            if INITIALIZED:
                search_emails_and_update_sheet(
                    gmail_service=gmail_service,
                    sheets_service=sheets_service,
                    sheet_id=sheet_id,
                    contact_df=contact_df,
                    query=f"after: {get_previous_day(last_run_date)}",
                )
            today = datetime.now().strftime("%Y/%m/%d")
            if today != last_run_date:
                file.write("\n" + datetime.now().strftime("%Y/%m/%d"))

        # summary = (
        #     openai.ChatCompletion.create(
        #         model="gpt-3.5-turbo",
        #         messages=[
        #             {
        #                 "role": "system",
        #                 "content": """I am Mihir Mishra. Refer to Mihir as "you". Summarize the emails I
        #             give you in 2 or fewer sentences. Never give me my own
        #             contact information. Use the email handle or email content to fill out all information.
        #             Your output should be in the following format:

        #             Name
        #             Company (and possition if applicable)
        #             Contact Info
        #             Summary """,
        #             },
        #             {
        #                 "role": "user",
        #                 "content": f"{message}",
        #             },
        #         ],
        #         temperature=0.1,
        #     )
        #     .choices[0]  # type: ignore
        #     .message.content
        # )

        # print(summary)
        # print()

        # info = summary.split("\n")

        # # TODO: make this a more robust method of checking if contact already in sheet
        # if info[0] not in id_names_dict.values():
        #     row_data = [len(id_names_dict), info[0], info[1], info[2]]

        #     add_row(
        #         sheets_service,
        #         row_data=row_data,
        #         sheet_id=sheet_id,
        #         tab_name="Contacts",
        #     )
        #     sheet_vals = read_sheet(
        #         sheets_service, sheet_id, range="Contacts!A:B", axis="columns"
        #     )
        #     id_names_dict = {
        #         sheet_vals[0][i]: sheet_vals[1][i]
        #         for i in range(len(sheet_vals[0]))
        #     }

    except HttpError as error:
        # TODO(developer) - Handle errors from gmail API.
        print(f"An error occurred: {error}")


if __name__ == "__main__":
    main()
