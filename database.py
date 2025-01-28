from peewee import (
    Model, CharField, BooleanField, IntegerField, DateTimeField,
    ForeignKeyField, TextField, DateField, TimeField, AutoField,
    Check, fn, SQL, Field
)
from playhouse.sqlite_ext import SqliteExtDatabase
import json
from datetime import datetime, timedelta
import requests
from fake_useragent import FakeUserAgent
from logger import logging

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
    headers = JSONField(default={
            'accept': "*/*",
            'content-type': "application/json",
            'accept-charset': "utf-8, *;q=0.8",
            'accept-language': "en-us",
            'user-agent': 'TEST word'
        })


    class Meta:
        database = db
    

    def __login(self) -> dict|None:
        url = "http://api-mobile.nz.ua/v1/user/login"
        payload = {
            "username": self.login,
            "password": self.password
        }

        response = requests.post(url, json=payload, headers=self.headers)
        if response.status_code == 200:
            data = response.json()
            self.FIO = data['FIO']
            self.token_expired = data['expires_token']
            self.student_id = data['student_id']
            self.headers['authorization'] = f"Bearer {data['access_token']}"
            self.save()
            return data
        else:
            raise Exception(f'Произошла ошибка авторизации {response.status_code}')
        
    def credentials(self, login:str, password:str):
        self.login, self.password = (login, password)
        self.save()
        return self.__login()
    
    def _check_token_expire(self):
        month = int(datetime.now().timestamp() + timedelta(days=25).total_seconds())
        if self.token_expired <= month:
            self.__login()

    def _fetch_diary(self, dates: list) -> dict|None:
        return self.__fetch_empty('http://api-mobile.nz.ua/v1/schedule/diary', dates)
    def _fetch_timetable(self, dates: list) -> dict|None:
        return self.__fetch_empty('http://api-mobile.nz.ua/v1/schedule/timetable', dates)
    def _fetch_student_performance(self, dates: list) -> dict|None:
        return self.__fetch_empty('http://api-mobile.nz.ua/v1/schedule/student-performance', dates)
    def _fetch_missed_lessons(self, dates: list) -> dict|None:
        return self.__fetch_empty('http://api-mobile.nz.ua/v1/schedule/missed-lessons', dates)
    def __fetch_empty(self, url:str, dates: list) -> dict|None:
        if len(dates) == 1:
            dates.append(dates[0])
        elif len(dates) != 2:
            raise ArithmeticError("Лист должен содержать 1 либо 2 даты")
        
        payload = {
            "start_date": dates[0], 
            "end_date": dates[1], 
            "student_id": self.student_id
        }

        response = requests.post(url, headers=self.headers, json=payload)
        response.raise_for_status()
        if response.status_code == 200:
            return response.json()
        else:
            logging.critical(f'Произошла ошибка получения {url[27:]} {response.status_code}')  
            raise Exception(f'Произошла ошибка получения {url[27:]} {response.status_code}')  
    def get_new_grades(self):
        try:
            self._check_token_expire()

            today = datetime.now() - timedelta(days=7)
            start_date = today.strftime("%Y-%m-%d")
            end_date = (today + timedelta(days=13)).strftime("%Y-%m-%d")

            all_grades = []
            request = self._fetch_student_performance([start_date, end_date])
            subjects = request.get("subjects", [])
            if not subjects:
                return

            for subject in subjects:
                grades_response = requests.post(
                    "http://api-mobile.nz.ua/v1/schedule/subject-grades",
                    headers=self.headers,
                    json={
                        "student_id": self.student_id,
                        "subject_id": subject["subject_id"],
                        "start_date": start_date,
                        "end_date": end_date
                    }
                )
                grades_response.raise_for_status()
                all_grades.extend(grades_response.json().get("lessons", []))

            changes = {
                "new_grades": [],
                "updated_grades": [],
                "deleted_grades": []
            }
            
            previous_lessons = self.last_marks.get("lessons", [])
            previous_lesson_ids = {lesson['lesson_id'] for lesson in previous_lessons}
            current_lesson_ids = {lesson['lesson_id'] for lesson in all_grades}

            deleted_lesson_ids = previous_lesson_ids - current_lesson_ids

            for lesson_id in deleted_lesson_ids:
                deleted_lesson = next((item for item in previous_lessons if item["lesson_id"] == lesson_id), None)
                if deleted_lesson:
                  changes["deleted_grades"].append(deleted_lesson)

            for grade in all_grades:
                previous_grade = next((item for item in previous_lessons if item["lesson_id"] == grade["lesson_id"]), None)

                if previous_grade:
                    if grade != previous_grade:
                        changes["updated_grades"].append({"new": grade, "old": previous_grade}) # сохраняем и старую, и новую оценку
                else:
                    changes["new_grades"].append(grade)

            self.last_marks = {"lessons": all_grades}
            self.save()
            
            return changes

        except requests.exceptions.RequestException as e:
            print(f"Error during API request: {e}")
            return None
        except json.JSONDecodeError as e:
            print(f"Error decoding JSON response: {e}")
            return None
        except ValueError as e:
            print(e)
            return None

        


    def __str__(self):
        return ...
    
def create_tables():
    with db:
        db.create_tables([
            User
        ])

if __name__ == "__main__":
    create_tables()