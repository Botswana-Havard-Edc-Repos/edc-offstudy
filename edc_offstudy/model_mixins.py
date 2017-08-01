from django.apps import apps as django_apps
from django.db import models
from django.db.models import options
from django.utils import timezone

from edc_base.model_fields import OtherCharField
from edc_base.model_validators import datetime_not_future
from edc_identifier.model_mixins import UniqueSubjectIdentifierFieldMixin
from edc_protocol.validators import datetime_not_before_study_start
from edc_visit_schedule import site_visit_schedules
from edc_visit_schedule.model_mixins import VisitScheduleMethodsModelMixin

options.DEFAULT_NAMES = options.DEFAULT_NAMES + ('consent_model', )


class OffstudyError(Exception):
    pass


class OffstudyModelManager(models.Manager):

    def get_by_natural_key(self, subject_identifier):
        return self.get(subject_identifier=subject_identifier)


class OffstudyModelMixin(UniqueSubjectIdentifierFieldMixin,
                         VisitScheduleMethodsModelMixin, models.Model):
    """Mixin for the Off Study model.
    """
    dateformat = '%Y-%m-%d %H:%M'

    offstudy_datetime = models.DateTimeField(
        verbose_name="Off-study Date",
        validators=[
            datetime_not_before_study_start,
            datetime_not_future])

    reason = models.CharField(
        verbose_name="Please code the primary reason participant taken off-study",
        max_length=115)

    reason_other = OtherCharField()

    comment = models.TextField(
        max_length=250,
        verbose_name="Comment",
        blank=True,
        null=True)

    objects = OffstudyModelManager()

    def save(self, *args, **kwargs):
        if not self.consented_before_offstudy:
            raise OffstudyError(
                'Offstudy date may not be before the date of consent. Got {}.'.format(
                    timezone.localtime(self.offstudy_datetime).strftime(self.dateformat)))
        self.offstudy_datetime_after_last_visit_or_raise()
        app_config = django_apps.get_app_config('edc_appointment')
        Appointment = app_config.model
        Appointment.objects.delete_for_subject_after_date(
            self.subject_identifier, self.offstudy_datetime)
        super().save(*args, **kwargs)

    def natural_key(self):
        return (self.subject_identifier, )

    def __str__(self):
        return "{0} {1}".format(
            self.subject_identifier,
            timezone.localtime(self.offstudy_datetime).strftime(self.dateformat))

    @property
    def consented_before_offstudy(self):
        consent = None
        try:
            Consent = django_apps.get_model(
                *self._meta.consent_model.split('.'))
            consent = Consent.objects.get(
                subject_identifier=self.subject_identifier,
                consent_datetime__lte=self.offstudy_datetime)
        except Consent.DoesNotExist:
            consent = None
        except AttributeError as e:
            if 'consent_model' in str(e):
                raise AttributeError(
                    'For model {} got: {}'.format(self._meta.label_lower, str(e)))
            raise OffstudyError(str(e))
        return consent

    def offstudy_datetime_after_last_visit_or_raise(self):
        try:
            last_visit = site_visit_schedules.visits(
                self.subject_identifier)[-1:][0]
            if (last_visit.report_datetime - self.offstudy_datetime).days > 0:
                raise OffstudyError(
                    'Offstudy datetime cannot precede the last visit datetime {}. Got {}'.format(
                        timezone.localtime(last_visit.report_datetime),
                        timezone.localtime(self.offstudy_datetime)))
        except AttributeError as e:
            raise OffstudyError(str(e))
        except IndexError as e:
            raise OffstudyError(str(e))

    class Meta:
        abstract = True
        consent_model = None


class OffstudyMixin(VisitScheduleMethodsModelMixin, models.Model):

    """A mixin for CRF models to add the ability to determine
    if the subject is off study.
    """

    def save(self, *args, **kwargs):
        self.is_offstudy_or_raise()
        super(OffstudyMixin, self).save(*args, **kwargs)

    @property
    def offstudy_model(self):
        # FIXME: if you get an AttributeError, is self.visit_schedule
        # not going to just raise another. Use the visit schedule methods
        # mixin? If instance is being saved for the first time???
        offstudy_model = None
        try:
            offstudy_model = self.visit.visit_schedule.models.get(
                'offstudy_model')
        except AttributeError as e:
            if 'visit' in str(e):
                try:
                    offstudy_model = self.visit_schedule.offstudy_model
                except AttributeError as e:
                    raise OffstudyError(str(e))
            else:
                raise OffstudyError(str(e))
        return django_apps.get_model(*offstudy_model.split('.'))

    def is_offstudy_or_raise(self):
        """Return True if the off-study report exists. """
        try:
            offstudy = self.offstudy_model.objects.get(
                offstudy_datetime__lte=self.report_datetime,
                subject_identifier=self.subject_identifier
            )
            raise OffstudyError(
                'Participant was reported off study on \'{0}\'. '
                'Data reported after this date'
                ' cannot be captured.'.format(timezone.localtime(
                    offstudy.offstudy_datetime).strftime('%Y-%m-%d')))
        except self.offstudy_model.DoesNotExist:
            offstudy = None
        return offstudy

    class Meta:
        abstract = True
