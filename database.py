import json
from datetime import datetime, timedelta

import aiohttp
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from peewee import (
    Model, CharField, IntegerField,
    ForeignKeyField, TextField, AutoField, Field
)
from playhouse.sqlite_ext import SqliteExtDatabase
from logger import logging
import io


db = SqliteExtDatabase('database.db', pragmas={'foreign_keys': 1})


class JSONField(TextField):
    def python_value(self, value):
        if value is not None:
            return json.loads(value)
        return {}

    def db_value(self, value):
        if value:
            return json.dumps(value)
        return '{}'


class User(Model):
    id = IntegerField(primary_key=True, unique=True)
    FIO = CharField(null=True)
    token_expired = IntegerField(null=True)
    student_id = IntegerField(null=True)
    login = CharField(null=True)
    password = CharField(null=True)
    last_marks = JSONField(default={})
    mig = JSONField(default={})
    headers = JSONField(default={
        'accept': "*/*",
        'content-type': "application/json",
        'accept-charset': "utf-8, *;q=0.8",
        'accept-language': "en-us",
        'user-agent': 'NzUA_Mobile_Client/2.1.5 (iPhone; iOS 16.0.2; Scale/3.00)'
    })

    class Meta:
        database = db

    async def __login(self, session: aiohttp.ClientSession) -> dict | None:
        url = "http://api-mobile.nz.ua/v1/user/login"
        payload = {
            "username": self.login,
            "password": self.password
        }

        async with session.post(url, json=payload, headers=self.headers) as response:
            if response.status == 200:
                data = await response.json()
                self.FIO = data['FIO']
                self.token_expired = data['expires_token']
                self.student_id = data['student_id']
                self.headers['authorization'] = f"Bearer {data['access_token']}"
                self.save()
                return data
            else:
                raise Exception(f'Произошла ошибка авторизации {response.status}')

    async def credentials(self, login: str, password: str, session: aiohttp.ClientSession):
        self.login, self.password = (login, password)
        self.save()
        return await self.__login(session)

    async def _check_token_expire(self, session: aiohttp.ClientSession):
        month = int(datetime.now().timestamp() + timedelta(days=25).total_seconds())
        if self.token_expired <= month:
            await self.__login(session)

    async def _fetch_data(self, url: str, dates: list, session: aiohttp.ClientSession) -> dict | None:
        if len(dates) == 1:
            dates.append(dates[0])
        elif len(dates) != 2:
            raise ArithmeticError("Лист должен содержать 1 либо 2 даты")

        payload = {
            "start_date": dates[0],
            "end_date": dates[1],
            "student_id": self.student_id
        }

        async with session.post(url, headers=self.headers, json=payload) as response:
            if response.status == 200:
                return await response.json()
            else:
                logging.critical(
                    f'Произошла ошибка получения {url[27:]} {response.status}')
                raise Exception(
                    f'Произошла ошибка получения {url[27:]} {response.status}')

    async def _fetch_grades(self, dates: list, subject: int, session: aiohttp.ClientSession):
        if len(dates) == 1:
            dates.append(dates[0])
        elif len(dates) != 2:
            raise ArithmeticError("Лист должен содержать 1 либо 2 даты")
        payload = {
            "student_id": self.student_id,
            "subject_id": subject,
            "start_date": dates[0],
            "end_date": dates[1]
        }
        async with session.post("http://api-mobile.nz.ua/v1/schedule/subject-grades",
                               headers=self.headers, json=payload) as grades_response:
            grades_response.raise_for_status()
            return await grades_response.json()
    async def _fetch_new_api_data(self, session: aiohttp.ClientSession):
        url = 'http://api-mobile.nz.ua/v1/notifications/last-notifications?limit=20'
        headers = {
            'Authorization': f'Bearer {self.token}'
        }

        async with session.get(url, headers=headers) as response:
            response.raise_for_status()
            return await response.json()

    async def get_new_grades(self, session: aiohttp.ClientSession):
        try:
            await self._check_token_expire(session)

            new_api_response = await self._fetch_new_api_data(session)
            if not new_api_response or 'data' not in new_api_response:
                print("Error: Invalid response from new API")
                return None

            new_grades_data = new_api_response['data']

            all_grades = self._transform_new_api_data(new_grades_data)
            if all_grades is None:
                return None

            changes = self._compare_grades(all_grades)

            self.last_marks = {"lessons": all_grades}
            self.save()

            return changes

        except aiohttp.ClientError as e:
            print(f"Error during API request: {e}")
            return None
        except json.JSONDecodeError as e:
            print(f"Error decoding JSON response: {e}")
            return None
        except Exception as e:
            print(f"An unexpected error occurred: {e}")
            return None

    def _transform_new_api_data(self, new_api_data):
        transformed_grades = []
        for item in new_api_data:
            if item['data']['type'] == 'add-mark':
                try:
                    lesson_date = datetime.strptime(item['sentAt'], '%Y-%m-%d %H:%M:%S').strftime('%Y-%m-%d')
                    transformed_grades.append({
                        'lesson_id': item['id'],
                        'subject': item['data']['lessonName'],
                        'lesson_date': lesson_date,
                        'mark': item['data']['markValue'],
                        'lesson_type': item['data']['lessonType'],
                        'comment' : item['data']['comment']
                    })
                except (ValueError, KeyError) as e:
                    print(f"Error processing new API data item: {item}. Error: {e}")
                    return None
        return transformed_grades


    def _compare_grades(self, current_grades):
        changes = {
            "new_grades": [],
            "updated_grades": [],
            "deleted_grades": []
        }

        previous_lessons = self.last_marks.get("lessons", [])
        previous_lesson_ids = {lesson['lesson_id'] for lesson in previous_lessons}
        current_lesson_ids = {lesson['lesson_id'] for lesson in current_grades}
        deleted_lesson_ids = previous_lesson_ids - current_lesson_ids
        for lesson_id in deleted_lesson_ids:
            deleted_lesson = next((item for item in previous_lessons if item["lesson_id"] == lesson_id), None)
            if deleted_lesson:
                changes["deleted_grades"].append(deleted_lesson)

        for grade in current_grades:
            previous_grade = next((item for item in previous_lessons if item["lesson_id"] == grade["lesson_id"]), None)

            if previous_grade:
                if any(grade[key] != previous_grade[key] for key in grade if key != 'lesson_id'):
                    changes["updated_grades"].append({"new": grade, "old": previous_grade})
            else:
                changes["new_grades"].append(grade)

        return changes

    def generate_image(self):

        try:

            if not self.mig:
                logging.warning("No data available to generate table.")
                return None


            dates_for_table = sorted(set(date for subject_data in self.mig.values() for date in subject_data))
            table_data = []
            for subject, grades in self.mig.items():
                row = {'Предмет': subject}
                for date in dates_for_table:
                    row[date] = grades.get(date, '')
                table_data.append(row)


            df = pd.DataFrame(table_data).set_index('Предмет')
            if df.empty:
                logging.warning("No subjects found in the data.")
                return None


            fig, ax = plt.subplots(figsize=(12, len(df) * 0.5 + 1))
            fig.set_size_inches(8, 6)
            ax.axis('off')


            ax.text(0.01, 1.08, self.FIO, transform=ax.transAxes,
                    fontsize=14, fontweight='bold', va='top')
            ax.text(0.85, 1.08, datetime.now().strftime("%B").title(), transform=ax.transAxes,
                    fontsize=14, fontweight='bold', va='top')


            table = ax.table(cellText=df.values,
                            colLabels=df.columns,
                            rowLabels=df.index,
                            cellLoc='center',
                            loc='center')

            table.auto_set_font_size(False)
            table.set_fontsize(10)
            table.scale(1, 1.2)


            header_color = mcolors.to_rgba('lightgray', alpha=0.5)
            index_color = mcolors.to_rgba('lightgray', alpha=0.3)
            for key, cell in table.get_celld().items():
                if key[0] == 0:
                    cell.set_facecolor(header_color)
                    cell.set_text_props(fontweight='bold')
                if key[1] == -1:
                    cell.set_facecolor(index_color)
                    cell.set_text_props(fontweight='bold')

            plt.tight_layout()


            buf = io.BytesIO()
            plt.savefig(buf, format='png', dpi=300, bbox_inches='tight')
            plt.close(fig)
            return buf.getvalue()

        except Exception as e:
            logging.exception(f"Error generating image: {e}")
            return None


def create_tables():
    with db:
        db.create_tables([
            User
        ])


if __name__ == "__main__":
    create_tables()