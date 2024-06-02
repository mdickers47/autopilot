# homeassistant autopilot

Mimics and randomizes the recent usage history of your switch devices,
possibly making your house look lived in while you are gone.

## Details

This script will discover all of your switch devices.  For each one,
it retrieves the 10-day usage history and constructs a model.  The
model looks for "activations" in the history, where a switch was
turned on and then off.  The model contains a histogram of how many
activations were counted on each day in the training set, and the mean
and standard deviation of the start_time and duration of the
activation.

To "run" the model to generate a random day, we choose N=(number of
activations) randomly from the set of observations from the training
days.  Then for each activation, we generate a random start and stop
time assuming a Gaussian distribution from the observed mean and
stddev.

In "run" mode, the script will generate a day plan from each switch
model individually, compile and sort them, and and send the events to
the homeassistant `turn_on` and `turn_off` API services at the right
times.

## Oddities

For the "count activations by day" scheme to work, we need to choose a
time when the day begins (day_start).  If there is a lot of switch
activity near that time, the model will be bad.  This is because
activation start_time is measured as an offset from day_start.
Suppose day_start was 00:00h, and you have a light that sometimes
turns on at 23:30 and sometimes turns on at 00:30.  The offsets will
be recorded as 1800 and 84600, and the mean will be around noon, with
a giant variance.  This is not useful.

I am sure there is a fancy way to define "mean", or a clustering
algorithm, that would avoid this problem.  It is easier to set
day_start to something in the middle of the night and avoid it.
Thus day_start defaults to 04:00.

There are other things that this crude model will get wrong.  The
general strategy is to dump the model to a json file and make any
corrections by hand.

## Usage

`$ ./autopilot.py --model_file /model.json/ model`

Creates the model and writes it to a json file.  This is useful
because you will probably want to make some changes, such as to remove
devices that are not useful (e.g. bathroom fans).

`$ ./autopilot.py --model_file /model.json/ run`

Generates and executes one random day plan.

Other options:

`--url`
  the homeassistant URL, such as http://homeassistant.lan:8123

`--token_file`
  must contain the "long lived access token" that you create using the
  homeassistant UI

`--day_start`
  a clock time (such as "04:00") when the modeling day resets.  Should
  be a time that you are ~never awake and the house has the least
  amount of activity.  Note that day_start is baked into the model, so
  the value must not change between runs.

## Requirements

Requires python and access to a homeassistant REST API.

