package main

import (
	"encoding/json"
	"testing"
)

func TestQuestionDecodingAndPrivateAnswerKey(t *testing.T) {
	var q question
	input := `{"public":{"id":7,"question_type":"single_choice","points":2,"time_limit_seconds":20,"body":{"options":[{"id":"a"},{"id":"b"}]}},"answer_key":["b"]}`
	if err := json.Unmarshal([]byte(input), &q); err != nil { t.Fatal(err) }
	if q.Public.ID != 7 || q.Public.Points != 2 { t.Fatalf("bad question: %+v", q.Public) }
	if string(q.RawPublic) == "" { t.Fatal("public payload missing") }
	var exposed map[string]any
	if err := json.Unmarshal(q.RawPublic, &exposed); err != nil { t.Fatal(err) }
	if _, exists := exposed["answer_key"]; exists { t.Fatal("student payload contains key") }
	if ok, _ := isCorrect(q, json.RawMessage(`["b"]`)); !ok { t.Fatal("valid answer was not graded") }
	for _, invalid := range []string{`["b","b"]`, `["forged"]`, `{"answer":"b"}`, `["a"]`} {
		if ok, _ := isCorrect(q, json.RawMessage(invalid)); ok { t.Fatalf("incorrect answer accepted: %s", invalid) }
	}
}
