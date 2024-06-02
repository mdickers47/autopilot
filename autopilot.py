#!//usr/bin/python
"""
autopilot.py - Mimics and randomizes the recent usage of the "switch"
devices in homeassistant.  Requires access to a homeassistant REST API:

https://developers.home-assistant.io/docs/api/rest

See the README.md that should be nearby.

2024-06-01 Mikey Dickerson
"""

import argparse
import datetime # 100 revisions of python and this never gets better
import json
import os
import random
import requests
import statistics
import sys
import time


class ApiError(Exception): pass


def _normalize_ts(what):
  """we want to not care whether you have timestamps as floats
  (assumed to be POSIX and utc-based), or as datetime.datetime (with
  whatever tz is baked in), or as a string
  """
  if type(what) == float:
    what = datetime.datetime.fromtimestamp(what, tz=datetime.timezone.utc)
  if type(what) == datetime.datetime:
    what = what.isoformat(timespec='seconds')
  return what


class HassApi:

  def __init__(self, url, token_file):
    with open(token_file, 'r') as f:
      token = f.read().strip()
    self.headers = { 'Authorization': 'Bearer %s' % token,
                     'Content-type': 'application/json' }
    os.environ['http_proxy'] = '' # does not work through a proxy
    self.url = url + '/api'
    print('Checking connection to %s' % self.url)
    if not self.get('')['message'] == 'API running.':
      raise ApiError('could not contact %s' % self.url)

  def get(self, url, params=None):
    r = requests.get(self.url + '/' + url, headers=self.headers, params=params)
    if r.status_code != 200:
      raise ApiError('HTTP %s: %s' % (r.status_code, r.text))
    return r.json()  
      
  def get_history(self, entity, start_time, end_time):
    # we correct the dumbness in the api design
    start_time = _normalize_ts(start_time)
    end_time = _normalize_ts(end_time)
    r = self.get('history/period/%s' % start_time,
                 params={'filter_entity_id': entity,
                         'end_time': end_time,
                         'minimal_response': 1})
    history = []
    for state in r[0]:
      dt = datetime.datetime.fromisoformat(state['last_changed'])
      # note that i don't care about the microseconds
      history.append((int(dt.timestamp()), state['state']))
    return history

  def list_entities(self):
    ids = []
    for state in self.get('states'):
      ids.append(state['entity_id'])
    return ids

  def set_switch(self, entity_id, state):
    # nb, state better be 'on' or 'off'
    # also nb this api is bad
    url = self.url + '/services/switch/turn_' + state
    d = {'entity_id': entity_id}
    #print("url %s" % url)
    #print("headers %s" % self.headers)
    #print("data %s" % d)
    r = requests.post(url, headers=self.headers, json=d)
    if r.status_code != 200:
      print('HTTP error %s: %s' % (r.status_code, r.text))


class SwitchModel:

    def __init__(self):
      self.entity           = ''
      self.act_by_day       = [0] * 10
      self.act_start_mean   = 0.0
      self.duration_mean    = 0.0
      self.act_start_stdev  = 0.0
      self.duration_stdev   = 0.0

    def compute(self, api, entity, day_start):
      self.entity = entity
      # note that homeassistant only keeps 10 days of history by default,
      # which can be increased, but the folklore is that it will be unusably
      # slow over 30 days.
      window_end   = time.time()
      window_start = window_end - 86400*10
      print('searching %s history from %s to %s' %
            (entity, _normalize_ts(window_start), _normalize_ts(window_end)))
      h = api.get_history(entity, window_start, window_end)

      # read the timeseries to find "activations", which means the
      # switch was turned on for a while and then turned off.  we want
      # to know how many times this happens per day, the start
      # timestamps (as offsets from the "day start")
      act_times = []
      durations = []
      on_time = None
      # which day an "activation" counts on: defined as the number of
      # full day periods past the day_start that happened 11 days ago.
      time_base = datetime.datetime.now(tz=datetime.timezone.utc)
      time_base = time_base.astimezone(tz=None)
      time_base -= datetime.timedelta(days=11)
      time_base = replace_time(time_base, day_start)
      for change in h:
        if change[1] == 'off' and on_time:
          dt = datetime.datetime.fromtimestamp(on_time,
                                               tz=datetime.timezone.utc)
          dt = dt.astimezone(tz=None) # use local timezone
          act_date = (dt - time_base).days - 1
          act_date = min(9, act_date)
          self.act_by_day[act_date] += 1
          act_times.append((dt - time_base).seconds)
          durations.append(change[0] - on_time)
          print('activated on day %d at second %d for %d seconds' %
                (act_date, act_times[-1], durations[-1]))
          print('time_base is %s, dt is %s, diff is %s' % (time_base.isoformat(timespec='seconds'), dt.isoformat(timespec='seconds'), dt - time_base))
          on_time = None          
        elif change[1] == 'on':
          on_time = change[0]

      if len(act_times) == 0:
        return

      # find mean and stddev of start time and duration, which will be
      # convenient later in random.gauss().
      self.act_start_mean  = statistics.mean(act_times)
      self.duration_mean   = statistics.mean(durations)
      if len(act_times) > 1:
        self.act_start_stdev = statistics.stdev(act_times)
      if len(durations) > 1:
        self.duration_stdev  = statistics.stdev(durations)

    def __str__(self):
      if self.act_start_mean < 0.0001:
        return '%s inactive' % self.entity
      return('%s daily_hist %s start_time %.0f{%.1f} duration %.0f{%.1f}' %
             (self.entity, self.act_by_day, self.act_start_mean,
              self.act_start_stdev, self.duration_mean, self.duration_stdev))

    def generate(self):
      """create a random list of on/off events that resembles the model"""
      events = []
      for i in range(random.choice(self.act_by_day)):
        start    = int(random.gauss(self.act_start_mean, self.act_start_stdev))
        duration = int(random.gauss(self.duration_mean, self.duration_stdev))
        duration = max(duration, 5)
        start   = min(max(start, 0), 86000)
        end     = min(start + duration, 86399)
        events.append((start, self.entity, 'on'))
        events.append((end, self.entity, 'off'))
      return events

    def to_dict(self):
      return {'entity'         : self.entity,
              'act_by_day'     : self.act_by_day,
              'act_start_mean' : self.act_start_mean,
              'duration_mean'  : self.duration_mean,
              'act_start_stdev': self.act_start_stdev,
              'duration_stdev' : self.duration_stdev}

    def from_dict(self, j):
      self.entity          = j['entity']
      self.act_by_day      = j['act_by_day']
      self.act_start_mean  = j['act_start_mean']
      self.duration_mean   = j['duration_mean']
      self.act_start_stdev = j['act_start_stdev']
      self.duration_stdev  = j['duration_stdev']


def compact_events(events):
  """sort and remove useless events, where "useless" means setting the
  state to something it already is.
  """
  events.sort(key=lambda x:x[0])
  states = {}
  i = 0
  while i < len(events):
    _, entity, state = events[i]
    if entity in states and state == states[entity]:
      del events[i]
    else:
      states[entity] = state
      i += 1
  # add events to turn off anything left on
  for entity, state in states.items():
    if state == 'on':
      events.append((events[-1] + 5, entity, 'off'))
  # NB return is None because we modified events.


def replace_time(dt, tm):
  return dt.replace(hour=tm.hour, minute=tm.minute, second=tm.second)


def execute_plan(api, events, day_start):
  day_anchor = replace_time(datetime.datetime.now(), day_start)    
  while len(events) > 0:
    now = datetime.datetime.now()
    next_event = day_anchor + datetime.timedelta(seconds=events[0][0])
    event = events[0]
    if now > next_event:
      if (now - next_event).seconds < 180:
        print('%s executing %s->%s' %
              (now.isoformat(timespec='seconds'), event[1], event[2]))
        api.set_switch(event[1], event[2])
      else:
        print('%s skipping %s->%s (scheduled at %s)' %
              (now.isoformat(timespec='seconds'), event[1], event[2],
               next_event.isoformat(timespec='seconds')))
      del events[0]
    else:
      time.sleep(60)
  print('plan complete')


def calculate_models(api, day_start):
  switches = list(filter(lambda x: x.startswith('switch'),
                         api.list_entities()))
  models = []
  for s in switches:
    m = SwitchModel()
    m.compute(api, s, day_start)
    if m.act_start_mean > 0.001:
      models.append(m)
  return models


if __name__ == '__main__':

  parser = argparse.ArgumentParser(
      prog='hass_autopilot',
      description='Mimics the pattern of light usage in your lived-in house.')
  parser.add_argument('cmd', choices=['model', 'run'])
  parser.add_argument('--url',        default='http://hass.serenity.tpl:8123')
  parser.add_argument('--token_file', default='auth_token')
  parser.add_argument('--day_start',  default='04:00')
  parser.add_argument('--model_file', default='')
  args = parser.parse_args()
  day_start = datetime.time.fromisoformat(args.day_start)
  api = HassApi(args.url, args.token_file)

  if args.cmd == 'model':
    if not args.model_file:
      print('must specify --model_file')
      sys.exit(1)
    models = calculate_models(api, day_start)
    print('===> models')
    for m in models:
      print(m)
    with open(args.model_file, 'w') as mf:
      json.dump(models, mf, indent=2, default=lambda x: x.to_dict())

  elif args.cmd == 'run':
    if args.model_file:
      with open(args.model_file, 'r') as mf:
        j = json.load(mf)
        models = []
        for m_dict in j:
          m = SwitchModel()
          m.from_dict(m_dict)
          models.append(m)
    else:
      models = calculate_models(api, day_start)

    print('===> generating day plan')
    events = []
    for m in models:
      events.extend(m.generate())
    compact_events(events)

    day_anchor = replace_time(datetime.datetime.now(), day_start)
    for e in events:
      t = (day_anchor + datetime.timedelta(seconds=e[0]))\
          .isoformat(timespec='seconds')
      print('%s %-25s %s' % (t, e[1], e[2]))

    print('===> executing day plan')
    execute_plan(api, events, day_start)
